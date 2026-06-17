import Foundation
import AppKit          // NSWorkspace, NSPasteboard, NSAppleScript
import EventKit        // EKEventStore — calendar + reminders
import CoreServices    // NSMetadataQuery (Spotlight)
import ApplicationServices  // AXUIElement — focused-window title
import Darwin          // host_statistics / mach host APIs

/// Deep local context engine.
///
/// Periodically gathers a broad snapshot of what's happening on the user's Mac
/// (frontmost app/window, clipboard, recent files, Finder selection, git state,
/// calendar/reminders, system load) and POSTs it as JSON to a local server so a
/// coding agent can reason over the user's live working environment.
///
/// Every gather degrades gracefully: any field that requires a permission which
/// has been denied (Accessibility, AppleEvents, Calendar, Reminders) is simply
/// omitted — the engine never crashes and never blocks on a denied prompt.
@MainActor
final class ContextEngine: ObservableObject {

    // MARK: - Snapshot model

    /// One captured view of the machine. Encodes to the exact snake_case JSON
    /// shape the Python backend expects. Every field is optional/defaulted so a
    /// partial gather (e.g. Calendar denied) still encodes cleanly.
    struct Snapshot: Codable {
        var cwd: String?
        var frontmostApp: String?
        var frontmostWindow: String?
        var clipboard: String?
        var recentFiles: [String]?
        var finderSelection: [String]?
        var git: Git?
        var calendarToday: [String]?
        var reminders: [String]?
        var system: System?
        var capturedAt: String?

        struct Git: Codable {
            var branch: String?
            var dirty: Int?
            var lastCommit: String?

            enum CodingKeys: String, CodingKey {
                case branch
                case dirty
                case lastCommit = "last_commit"
            }
        }

        struct System: Codable {
            var cpuPct: Double?
            var memUsedGb: Double?
            var memTotalGb: Double?
            var thermal: String?

            enum CodingKeys: String, CodingKey {
                case cpuPct = "cpu_pct"
                case memUsedGb = "mem_used_gb"
                case memTotalGb = "mem_total_gb"
                case thermal
            }
        }

        enum CodingKeys: String, CodingKey {
            case cwd
            case frontmostApp = "frontmost_app"
            case frontmostWindow = "frontmost_window"
            case clipboard
            case recentFiles = "recent_files"
            case finderSelection = "finder_selection"
            case git
            case calendarToday = "calendar_today"
            case reminders
            case system
            case capturedAt = "captured_at"
        }
    }

    // MARK: - Published state

    /// The most recently gathered snapshot (drives UI). Updated on the main actor.
    @Published private(set) var latest: Snapshot?

    // MARK: - Private state

    /// Working directory to scan. Defaults to the project root, can be retargeted
    /// via `setCwd`. Read off-actor during gather, so kept as a plain String copy.
    private var cwd: String

    /// Repeating gather timer (20s). Lives on the main run loop.
    private var timer: Timer?

    /// EventKit store is created lazily and reused. nonisolated because EventKit
    /// calls are made from detached work; EKEventStore is safe to use this way.
    private let eventStore = EKEventStore()

    /// Shared session for fire-and-forget POSTs.
    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 5
        cfg.timeoutIntervalForResource = 10
        return URLSession(configuration: cfg)
    }()

    // MARK: - Init

    init(projectRoot: URL) {
        self.cwd = projectRoot.path
    }

    // MARK: - Public control surface

    /// Retarget the directory the engine scans for git/recent-files context.
    func setCwd(_ path: String) {
        cwd = path
    }

    /// Begin periodic gather+POST every 20s, firing once immediately.
    func start(port: Int) {
        stop()  // ensure we never stack timers
        // Fire immediately so the agent has context without waiting a full cycle.
        Task { await gatherNow(port: port) }
        let t = Timer(timeInterval: 20.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in await self.gatherNow(port: port) }
        }
        // common modes → keeps firing during menu/scroll tracking
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// Stop the periodic gather.
    func stop() {
        timer?.invalidate()
        timer = nil
    }

    /// Proactively request EventKit (Calendar + Reminders) and Contacts access.
    /// Best-effort — results are ignored here; the per-field gather re-checks
    /// authorization and simply omits anything denied.
    func requestPermissions() {
        let store = eventStore
        Task.detached {
            // macOS 14+ full-access entry points.
            _ = try? await store.requestFullAccessToEvents()
            _ = try? await store.requestFullAccessToReminders()
        }
        // Contacts is requested lazily/independently to avoid a hard dependency
        // on the Contacts framework import; EventKit covers the fields we emit.
    }

    // MARK: - Gather + POST

    /// Gather one snapshot, publish it to `latest`, and POST it to /v1/context.
    /// Fire-and-forget on the network side: POST failures are swallowed so the
    /// agent loop is never disrupted by a transient backend hiccup.
    @discardableResult
    func gatherNow(port: Int) async -> Snapshot {
        let scanPath = cwd
        var snap = Snapshot()
        snap.cwd = scanPath
        snap.capturedAt = Self.iso8601Now()

        // --- Main-actor / AppKit-bound fields (must run on main actor) ---
        snap.frontmostApp = NSWorkspace.shared.frontmostApplication?.localizedName
        snap.frontmostWindow = Self.focusedWindowTitle()
        snap.clipboard = Self.clipboardText()

        // --- Off-actor work runs concurrently to keep gather snappy ---
        // git (Process), recent files (FS/Spotlight), CPU sampling, and the
        // Finder AppleScript are all detached; EventKit fetches use the store.
        async let gitInfo = Self.gatherGit(at: scanPath)
        async let recents = Self.gatherRecentFiles(at: scanPath)
        async let finderSel = Self.gatherFinderSelection()
        async let sys = Self.gatherSystem()
        async let cal = gatherCalendarToday()
        async let rem = gatherReminders()

        snap.git = await gitInfo
        snap.recentFiles = await recents
        snap.finderSelection = await finderSel
        snap.system = await sys
        snap.calendarToday = await cal
        snap.reminders = await rem

        latest = snap
        Self.post(snap, port: port, session: session)
        return snap
    }

    /// JSON-encode and POST the snapshot. Errors are intentionally ignored.
    nonisolated private static func post(_ snap: Snapshot, port: Int, session: URLSession) {
        guard let url = URL(string: "http://127.0.0.1:\(port)/v1/context"),
              let body = try? JSONEncoder().encode(snap) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        session.dataTask(with: req).resume()  // fire-and-forget
    }

    // MARK: - Formatters

    /// ISO8601 timestamp for "now". A fresh formatter is created per call — it is
    /// cheap and avoids sharing non-Sendable formatter state across actors.
    nonisolated private static func iso8601Now() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: Date())
    }

    // MARK: - Clipboard

    /// First ~400 chars of the general pasteboard's text, if any.
    nonisolated private static func clipboardText() -> String? {
        guard let s = NSPasteboard.general.string(forType: .string) else { return nil }
        return String(s.prefix(400))
    }

    // MARK: - Frontmost window title (Accessibility)

    /// Focused-window title of the frontmost app via the Accessibility API.
    /// Returns nil (and never prompts aggressively) if the process is not
    /// AX-trusted or the title cannot be read.
    nonisolated private static func focusedWindowTitle() -> String? {
        // Only attempt if already trusted — passing prompt:true would nag the
        // user every gather, which the spec explicitly forbids.
        guard AXIsProcessTrusted() else { return nil }
        guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return nil }

        let appElement = AXUIElementCreateApplication(pid)

        // Prefer the explicit focused window; fall back to the app's main window.
        if let title = axStringAttribute(appElement, kAXFocusedWindowAttribute as CFString,
                                         then: kAXTitleAttribute as CFString) {
            return title
        }
        if let title = axStringAttribute(appElement, kAXMainWindowAttribute as CFString,
                                         then: kAXTitleAttribute as CFString) {
            return title
        }
        return nil
    }

    /// Read `outer` (a window ref) off `element`, then read string `inner` (its
    /// title) off that window. Returns nil on any failure.
    nonisolated private static func axStringAttribute(
        _ element: AXUIElement,
        _ outer: CFString,
        then inner: CFString
    ) -> String? {
        var windowRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, outer, &windowRef) == .success,
              let windowRef else { return nil }
        // windowRef is an AXUIElement (a CFType); cast through the opaque type.
        let window = unsafeBitCast(windowRef, to: AXUIElement.self)

        var titleRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(window, inner, &titleRef) == .success,
              let title = titleRef as? String,
              !title.isEmpty else { return nil }
        return title
    }

    // MARK: - Git

    /// Branch / dirty-count / last-commit for the repo at `path`, or nil if the
    /// directory is not a git working tree. Runs `git` via Process off-actor.
    nonisolated private static func gatherGit(at path: String) async -> Snapshot.Git? {
        // Cheap repo check first — avoids emitting an all-nil git object.
        guard let head = runGit(["rev-parse", "--abbrev-ref", "HEAD"], at: path),
              !head.isEmpty else { return nil }

        var git = Snapshot.Git()
        git.branch = head

        if let status = runGit(["status", "--porcelain"], at: path) {
            let lines = status.split(separator: "\n", omittingEmptySubsequences: true)
            git.dirty = lines.count
        }
        if let commit = runGit(["log", "-1", "--pretty=%h %s"], at: path), !commit.isEmpty {
            git.lastCommit = commit
        }
        return git
    }

    /// Run `/usr/bin/git -C <path> <args>` and return trimmed stdout, or nil on
    /// non-zero exit / launch failure.
    nonisolated private static func runGit(_ args: [String], at path: String) -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        proc.arguments = ["-C", path] + args
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do {
            try proc.run()
        } catch {
            return nil
        }
        // Read before wait to avoid pipe-buffer deadlock on large output.
        let data = out.fileHandleForReading.readDataToEndOfFile()
        _ = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else { return nil }
        let s = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return s
    }

    // MARK: - Recent files

    /// 10 most-recently-modified files under `path`. Tries Spotlight first, then
    /// falls back to a fast, capped recursive FileManager scan.
    nonisolated private static func gatherRecentFiles(at path: String) async -> [String]? {
        if let viaSpotlight = await spotlightRecentFiles(at: path), !viaSpotlight.isEmpty {
            return viaSpotlight
        }
        let viaScan = fileManagerRecentFiles(at: path)
        return viaScan.isEmpty ? nil : viaScan
    }

    /// NSMetadataQuery (Spotlight) scoped to `path`, sorted by content-change
    /// date descending, capped at 10. Returns nil if Spotlight yields nothing in
    /// a short window (the caller then falls back to a filesystem scan).
    ///
    /// The query, its observer, and the timeout are all driven on the main actor
    /// via `SpotlightProbe`, which keeps every piece of non-Sendable state on one
    /// actor and avoids leaking it across the concurrency boundary.
    nonisolated private static func spotlightRecentFiles(at path: String) async -> [String]? {
        await MainActor.run { SpotlightProbe(path: path) }.run()
    }

    /// Synchronous, capped recursive scan of `path`. Skips noisy build/vendor
    /// dirs, caps the number of files visited, and returns the 10 most recently
    /// modified file paths. Designed to stay fast even on large trees.
    nonisolated private static func fileManagerRecentFiles(at path: String) -> [String] {
        let skip: Set<String> = [".git", "node_modules", ".venv", "venv",
                                 "dist", "build", ".build", ".next", "__pycache__"]
        let fm = FileManager.default
        let root = URL(fileURLWithPath: path)
        let keys: [URLResourceKey] = [.contentModificationDateKey, .isDirectoryKey, .isRegularFileKey]

        guard let enumerator = fm.enumerator(
            at: root,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return [] }

        var candidates: [(path: String, date: Date)] = []
        var visited = 0
        let visitCap = 20_000  // hard ceiling on traversal cost

        for case let url as URL in enumerator {
            visited += 1
            if visited > visitCap { break }

            // Prune heavy directories.
            if skip.contains(url.lastPathComponent) {
                enumerator.skipDescendants()
                continue
            }
            guard let vals = try? url.resourceValues(forKeys: Set(keys)) else { continue }
            if vals.isDirectory == true { continue }
            guard vals.isRegularFile == true else { continue }
            let mod = vals.contentModificationDate ?? .distantPast
            candidates.append((url.path, mod))
        }

        return candidates
            .sorted { $0.date > $1.date }
            .prefix(10)
            .map(\.path)
    }

    // MARK: - Finder selection (AppleScript)

    /// Currently selected files in Finder as POSIX paths, via NSAppleScript.
    /// Returns nil if AppleEvents permission is denied or Finder has no
    /// selection — failures are swallowed (we never prompt aggressively).
    nonisolated private static func gatherFinderSelection() async -> [String]? {
        await withCheckedContinuation { continuation in
            // NSAppleScript must execute on the main thread.
            DispatchQueue.main.async {
                let source = """
                tell application "Finder"
                    set sel to selection as alias list
                    set out to ""
                    repeat with f in sel
                        set out to out & POSIX path of (f as alias) & linefeed
                    end repeat
                    return out
                end tell
                """
                guard let script = NSAppleScript(source: source) else {
                    continuation.resume(returning: nil); return
                }
                var errInfo: NSDictionary?
                let result = script.executeAndReturnError(&errInfo)
                if errInfo != nil {
                    // Permission denied (-1743) or Finder error → omit.
                    continuation.resume(returning: nil); return
                }
                let raw = result.stringValue ?? ""
                let paths = raw
                    .split(separator: "\n", omittingEmptySubsequences: true)
                    .map { String($0).trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
                continuation.resume(returning: paths.isEmpty ? nil : paths)
            }
        }
    }

    // MARK: - Calendar (EventKit)

    /// Today's events as "HH:mm Title" strings. Omitted if access is not
    /// authorized. Uses the macOS 14 full-access entry point.
    private func gatherCalendarToday() async -> [String]? {
        let store = eventStore
        // Read ONLY if the user has already granted access — never prompt.
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { return nil }

        return await Task.detached { () -> [String]? in
            let cal = Calendar.current
            let start = cal.startOfDay(for: Date())
            guard let end = cal.date(byAdding: .day, value: 1, to: start) else { return nil }

            let predicate = store.predicateForEvents(
                withStart: start, end: end, calendars: nil
            )
            let events = store.events(matching: predicate)
            guard !events.isEmpty else { return nil }

            let fmt = DateFormatter()
            fmt.dateFormat = "HH:mm"
            let lines = events
                .sorted { ($0.startDate ?? .distantPast) < ($1.startDate ?? .distantPast) }
                .map { ev -> String in
                    let title = ev.title ?? "(untitled)"
                    if ev.isAllDay { return "All-day \(title)" }
                    let time = ev.startDate.map { fmt.string(from: $0) } ?? "--:--"
                    return "\(time) \(title)"
                }
            return lines.isEmpty ? nil : lines
        }.value
    }

    // MARK: - Reminders (EventKit)

    /// Titles of incomplete reminders. Omitted if access is not authorized.
    private func gatherReminders() async -> [String]? {
        let store = eventStore
        // Read ONLY if the user has already granted access — never prompt.
        guard EKEventStore.authorizationStatus(for: .reminder) == .fullAccess else { return nil }

        let predicate = store.predicateForIncompleteReminders(
            withDueDateStarting: nil, ending: nil, calendars: nil
        )
        let reminders: [EKReminder] = await withCheckedContinuation { cont in
            store.fetchReminders(matching: predicate) { found in
                cont.resume(returning: found ?? [])
            }
        }
        let titles = reminders.compactMap { $0.title }.filter { !$0.isEmpty }
        return titles.isEmpty ? nil : titles
    }

    // MARK: - System metrics

    /// CPU %, memory used/total (GB), and thermal state. CPU is sampled across a
    /// short delta inside this async call so the percentage reflects "now".
    nonisolated private static func gatherSystem() async -> Snapshot.System {
        var sys = Snapshot.System()
        sys.cpuPct = await sampleCPUPercent()

        // Memory: used = (active + wired + compressed) pages * pagesize.
        if let (used, total) = memoryUsage() {
            sys.memUsedGb = (used * 10).rounded() / 10
            sys.memTotalGb = (total * 10).rounded() / 10
        } else {
            // Total is always available even if vm_stat fails.
            let total = Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824.0
            sys.memTotalGb = (total * 10).rounded() / 10
        }

        sys.thermal = thermalString()
        return sys
    }

    /// Map ProcessInfo thermal state to the backend's vocabulary.
    nonisolated private static func thermalString() -> String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal:  return "nominal"
        case .fair:     return "fair"
        case .serious:  return "serious"
        case .critical: return "critical"
        @unknown default: return "nominal"
        }
    }

    /// Total physical memory used (GB) and total (GB), via host_statistics64.
    nonisolated private static func memoryUsage() -> (used: Double, total: Double)? {
        let totalBytes = Double(ProcessInfo.processInfo.physicalMemory)
        guard totalBytes > 0 else { return nil }

        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(
            MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size
        )
        let host = mach_host_self()
        let kr = withUnsafeMutablePointer(to: &stats) { ptr -> kern_return_t in
            ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
                host_statistics64(host, HOST_VM_INFO64, intPtr, &count)
            }
        }
        guard kr == KERN_SUCCESS else { return nil }

        var pageSize: vm_size_t = 0
        host_page_size(host, &pageSize)
        let ps = Double(pageSize)

        let activeBytes     = Double(stats.active_count) * ps
        let wiredBytes      = Double(stats.wire_count) * ps
        let compressedBytes = Double(stats.compressor_page_count) * ps
        let usedBytes = activeBytes + wiredBytes + compressedBytes

        let gb = 1_073_741_824.0
        return (usedBytes / gb, totalBytes / gb)
    }

    /// Sample CPU busy % across two host_cpu_load_info reads ~200ms apart.
    /// Returns a system-wide percentage (0–100) rounded to one decimal.
    nonisolated private static func sampleCPUPercent() async -> Double? {
        guard let first = cpuTicks() else { return nil }
        try? await Task.sleep(nanoseconds: 200_000_000)  // 200ms delta
        guard let second = cpuTicks() else { return nil }

        let userDelta = Double(second.user - first.user)
        let sysDelta  = Double(second.system - first.system)
        let niceDelta = Double(second.nice - first.nice)
        let idleDelta = Double(second.idle - first.idle)
        let busy = userDelta + sysDelta + niceDelta
        let totalDelta = busy + idleDelta
        guard totalDelta > 0 else { return nil }

        let pct = (busy / totalDelta) * 100.0
        return (pct * 10).rounded() / 10
    }

    /// Aggregate CPU tick counters (host_cpu_load_info).
    nonisolated private static func cpuTicks()
        -> (user: natural_t, system: natural_t, idle: natural_t, nice: natural_t)? {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(
            MemoryLayout<host_cpu_load_info_data_t>.size / MemoryLayout<integer_t>.size
        )
        let host = mach_host_self()
        let kr = withUnsafeMutablePointer(to: &info) { ptr -> kern_return_t in
            ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
                host_statistics(host, HOST_CPU_LOAD_INFO, intPtr, &count)
            }
        }
        guard kr == KERN_SUCCESS else { return nil }
        return (
            user:   info.cpu_ticks.0,  // CPU_STATE_USER
            system: info.cpu_ticks.1,  // CPU_STATE_SYSTEM
            idle:   info.cpu_ticks.2,  // CPU_STATE_IDLE
            nice:   info.cpu_ticks.3   // CPU_STATE_NICE
        )
    }
}

// MARK: - Spotlight probe

/// Main-actor-isolated wrapper around a one-shot NSMetadataQuery.
///
/// NSMetadataQuery must be driven on a run loop and is not Sendable, so all of
/// its state (query, observer token, timeout) is confined to the main actor.
/// `run()` returns up to 10 most-recently-changed file paths under `path`, or
/// nil if Spotlight produces nothing within a short safety window.
@MainActor
private final class SpotlightProbe {
    private let path: String
    private let query = NSMetadataQuery()
    private var observer: NSObjectProtocol?
    private var timeoutTask: Task<Void, Never>?
    private var finished = false
    private var continuation: CheckedContinuation<[String]?, Never>?

    init(path: String) {
        self.path = path
    }

    func run() async -> [String]? {
        await withCheckedContinuation { (cont: CheckedContinuation<[String]?, Never>) in
            self.continuation = cont

            query.searchScopes = [URL(fileURLWithPath: path)]
            query.predicate = NSPredicate(format: "%K LIKE '*'", NSMetadataItemFSNameKey)
            query.sortDescriptors = [
                NSSortDescriptor(key: NSMetadataItemFSContentChangeDateKey, ascending: false)
            ]

            observer = NotificationCenter.default.addObserver(
                forName: .NSMetadataQueryDidFinishGathering,
                object: query,
                queue: .main
            ) { [weak self] _ in
                // queue: .main delivers on the main thread → hop to the actor.
                MainActor.assumeIsolated { self?.finishWithResults() }
            }

            // Safety timeout so a stalled Spotlight never hangs the gather.
            timeoutTask = Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 2_500_000_000)
                guard !Task.isCancelled else { return }
                self?.finish(nil)
            }

            if !query.start() {
                finish(nil)
            }
        }
    }

    private func finishWithResults() {
        var paths: [String] = []
        for i in 0..<min(query.resultCount, 10) {
            if let item = query.result(at: i) as? NSMetadataItem,
               let p = item.value(forAttribute: NSMetadataItemPathKey) as? String {
                paths.append(p)
            }
        }
        finish(paths.isEmpty ? nil : paths)
    }

    /// Resume exactly once; tear down query, observer, and timeout.
    private func finish(_ result: [String]?) {
        guard !finished else { return }
        finished = true
        query.stop()
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        timeoutTask?.cancel()
        timeoutTask = nil
        continuation?.resume(returning: result)
        continuation = nil
    }
}
