import Foundation
import AppKit
import Combine

@MainActor
final class AppState: ObservableObject {
    let server: ServerManager
    let context: ContextEngine
    @Published var cwd: String

    private let projectRoot: URL
    private var bag = Set<AnyCancellable>()

    init() {
        // Resolve the project root: prefer ~/scroll, else next to the bundle.
        let candidate = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("scroll")
        let root = FileManager.default.fileExists(atPath: candidate.path)
            ? candidate
            : Bundle.main.bundleURL.deletingLastPathComponent()
        self.projectRoot = root
        self.cwd = root.path
        self.server = ServerManager(projectRoot: root)
        self.context = ContextEngine(projectRoot: root)

        // Start the context engine once services are ready.
        server.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] s in
                guard let self else { return }
                if s == .ready { self.context.start(port: self.server.port) }
            }
            .store(in: &bag)
    }

    func boot() {
        server.boot()
        context.requestPermissions()
    }

    func shutdown() {
        context.stop()
        server.stop()
    }

    func pickWorkingDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Set Working Directory"
        panel.begin { [weak self] resp in
            guard resp == .OK, let url = panel.url else { return }
            Task { @MainActor in
                self?.cwd = url.path
                self?.context.setCwd(url.path)
            }
        }
    }
}
