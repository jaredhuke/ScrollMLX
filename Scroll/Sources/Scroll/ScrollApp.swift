import SwiftUI
import AppKit

@main
struct ScrollApp: App {
    @StateObject private var app = AppState()

    var body: some Scene {
        WindowGroup {
            RootView(app: app)
                .frame(minWidth: 900, minHeight: 640)
                .onAppear { app.boot() }
                .onDisappear { app.shutdown() }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1280, height: 840)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("Agent") {
                Button("Restart Services") { app.server.restart() }
                Button("Check for Updates") { app.server.checkForUpdates() }
                Divider()
                Button("Change Working Directory…") { app.pickWorkingDirectory() }
                    .keyboardShortcut("o", modifiers: [.command, .shift])
            }
        }

        MenuBarExtra("MLX", systemImage: "brain.head.profile") {
            MenuContent(server: app.server) { app.shutdown(); NSApplication.shared.terminate(nil) }
        }
    }
}

/// Swaps between the boot screen and the live web UI as services come up.
struct RootView: View {
    @ObservedObject var app: AppState
    @ObservedObject var server: ServerManager

    init(app: AppState) {
        self.app = app
        self.server = app.server
    }

    var body: some View {
        Group {
            if server.state == .ready {
                WebHostView(port: server.port, context: app.context)
                    .ignoresSafeArea()
            } else {
                BootView(server: server)
            }
        }
    }
}

struct MenuContent: View {
    @ObservedObject var server: ServerManager
    let quit: () -> Void

    var body: some View {
        Text(server.state == .ready ? "● Services running" : "Starting…")
        if let u = server.updateAvailable {
            Button("Update available — install") { server.applyUpdate() }
            Text(u).font(.caption)
        }
        Divider()
        Button("Restart services") { server.restart() }
        Button("Quit Scroll") { quit() }
    }
}
