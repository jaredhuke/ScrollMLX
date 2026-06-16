import Foundation
import Combine

enum ServerState: Equatable {
    case booting, ready
    case error(String)
}

enum StepState { case pending, running, done, failed }

struct BootStep: Identifiable {
    let id = UUID()
    let key: String
    var label: String
    var state: StepState = .pending
}

/// Owns the local Python services: locates the runtime, checks for updates,
/// launches uvicorn, and reports a step-by-step boot status. One-click product.
final class ServerManager: ObservableObject {
    @Published var state: ServerState = .booting
    @Published var steps: [BootStep] = []
    @Published var logs: [String] = []
    @Published var updateAvailable: String? = nil   // commit subject if an update exists

    private var process: Process?
    private let projectRoot: URL

    init(projectRoot: URL) {
        self.projectRoot = projectRoot
        self.steps = [
            BootStep(key: "runtime",  label: "Locating local runtime"),
            BootStep(key: "update",   label: "Checking for updates"),
            BootStep(key: "services", label: "Starting local services"),
            BootStep(key: "model",    label: "Loading the model"),
        ]
    }

    var port: Int { Int(ProcessInfo.processInfo.environment["MLX_PORT"] ?? "8080") ?? 8080 }

    // MARK: - Boot sequence

    func boot() {
        guard process == nil else { return }
        state = .booting
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in self?.runBoot() }
    }

    private func runBoot() {
        setStep("runtime", .running)
        guard let uvPath = locateUV() else {
            setStep("runtime", .failed)
            fail("`uv` not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh")
            return
        }
        setStep("runtime", .done)

        setStep("update", .running)
        checkForUpdates()          // non-fatal
        setStep("update", .done)

        setStep("services", .running)
        // If a healthy Scroll server is already on the port (a previous instance, a
        // manual `uvicorn`, or the relay's server), reuse it instead of launching a
        // second one and failing with "address already in use".
        if probeHealthSync() {
            log("A Scroll server is already running on :\(port) — reusing it.")
            setStep("services", .done); setStep("model", .done)
            DispatchQueue.main.async { self.state = .ready }
            return
        }
        do {
            try launchServer(uvPath: uvPath)
            setStep("services", .done)
        } catch {
            setStep("services", .failed)
            fail("Failed to launch services: \(error.localizedDescription)")
            return
        }

        setStep("model", .running)
        pollHealth()               // model finishes loading in the server's lifespan
    }

    /// Blocking health probe (runs on the boot thread) — true if a server already answers on the port.
    private func probeHealthSync(timeout: TimeInterval = 1.5) -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/health") else { return false }
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        let task = URLSession.shared.dataTask(with: url) { _, resp, _ in
            if let r = resp as? HTTPURLResponse, r.statusCode == 200 { ok = true }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + timeout)
        return ok
    }

    private func locateUV() -> String? {
        let home = NSHomeDirectory()
        let candidates = [
            "\(home)/.local/bin/uv", "\(home)/.cargo/bin/uv",
            "/opt/homebrew/bin/uv", "/usr/local/bin/uv", "/usr/bin/uv",
        ]
        return candidates.first(where: { FileManager.default.fileExists(atPath: $0) })
    }

    private func launchServer(uvPath: String) throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: uvPath)
        proc.arguments = ["run", "uvicorn", "server.main:app",
                          "--host", "127.0.0.1", "--port", "\(port)", "--no-access-log"]
        proc.currentDirectoryURL = projectRoot
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        proc.environment = env

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.log(line.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self else { return }
                self.process = nil
                let why = self.logs.suffix(3).joined(separator: " ⏎ ")
                self.log("Services stopped (exit \(p.terminationStatus)).")

                // Port already taken by a non-Scroll process (the reuse-probe ruled out a healthy Scroll server).
                if why.localizedCaseInsensitiveContains("address already in use") {
                    self.state = .error("Port \(self.port) is already in use by another app. Quit it, or run:  lsof -ti:\(self.port) | xargs kill  — then reopen Scroll.")
                    return
                }
                // One automatic restart for transient deaths (e.g. a Metal OOM abort mid-generation).
                if !self.didAutoRestart {
                    self.didAutoRestart = true
                    self.log("Restarting services once…")
                    self.boot()
                    return
                }
                let hint = why.localizedCaseInsensitiveContains("memory")
                    ? " — the model ran out of memory; try a shorter request."
                    : ""
                self.state = .error("Services exited unexpectedly\(hint) See ~/.scroll/server.log. Last: \(why)")
            }
        }
        try proc.run()
        self.process = proc
    }
    private var didAutoRestart = false

    private func pollHealth(attempts: Int = 0) {
        guard attempts < 120 else { setStep("model", .failed); fail("Model did not finish loading in time."); return }
        let url = URL(string: "http://127.0.0.1:\(port)/health")!
        URLSession.shared.dataTask(with: url) { [weak self] _, resp, _ in
            guard let self else { return }
            if let r = resp as? HTTPURLResponse, r.statusCode == 200 {
                DispatchQueue.main.async { self.setStep("model", .done); self.state = .ready; self.log("Ready.") }
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.pollHealth(attempts: attempts + 1) }
            }
        }.resume()
    }

    // MARK: - Updates (git-based, since the product ships as this repo)

    func checkForUpdates() {
        guard run(["/usr/bin/git", "-C", projectRoot.path, "fetch", "--quiet"]).ok else { return }
        let count = run(["/usr/bin/git", "-C", projectRoot.path, "rev-list", "--count", "HEAD..@{u}"])
            .out.trimmingCharacters(in: .whitespacesAndNewlines)
        if let n = Int(count), n > 0 {
            let subj = run(["/usr/bin/git", "-C", projectRoot.path, "log", "-1", "--pretty=%s", "@{u}"])
                .out.trimmingCharacters(in: .whitespacesAndNewlines)
            DispatchQueue.main.async { self.updateAvailable = subj.isEmpty ? "\(n) update(s) available" : subj }
        }
    }

    /// Pull the latest and restart services.
    func applyUpdate() {
        DispatchQueue.global().async { [weak self] in
            guard let self else { return }
            _ = self.run(["/usr/bin/git", "-C", self.projectRoot.path, "pull", "--ff-only"])
            DispatchQueue.main.async { self.updateAvailable = nil; self.restart() }
        }
    }

    func restart() { stop(); steps.indices.forEach { steps[$0].state = .pending }; boot() }

    func stop() {
        process?.terminate()
        process = nil
    }

    // MARK: - Helpers

    @discardableResult
    private func run(_ argv: [String]) -> (ok: Bool, out: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: argv[0])
        p.arguments = Array(argv.dropFirst())
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = Pipe()
        do { try p.run(); p.waitUntilExit() } catch { return (false, "") }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return (p.terminationStatus == 0, String(data: data, encoding: .utf8) ?? "")
    }

    private func setStep(_ key: String, _ s: StepState) {
        DispatchQueue.main.async {
            if let i = self.steps.firstIndex(where: { $0.key == key }) { self.steps[i].state = s }
        }
    }
    private func fail(_ msg: String) { DispatchQueue.main.async { self.state = .error(msg); self.log("ERROR: \(msg)") } }
    private func log(_ s: String) { logs.append(s); if logs.count > 300 { logs.removeFirst() } }
}
