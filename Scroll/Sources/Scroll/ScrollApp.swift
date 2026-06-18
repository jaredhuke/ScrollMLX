import SwiftUI
import AppKit

/// Forces the Dock icon at launch from the bundled image — robust even if the asset
/// catalog wasn't compiled (e.g. a SwiftPM run) or macOS cached an old/blank icon.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ note: Notification) {
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "png"),
           let img = NSImage(contentsOf: url) {
            NSApplication.shared.applicationIconImage = img
        } else if let img = NSImage(named: "AppIcon") {
            NSApplication.shared.applicationIconImage = img
        }
    }
}

@main
struct ScrollApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
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
                Button("API Keys…") { app.showKeys = true }
                    .keyboardShortcut("k", modifiers: .command)
                Divider()
                Button("Restart Services") { app.server.restart() }
                Button("Check for Updates") { app.server.checkForUpdates() }
                Divider()
                Button("Change Working Directory…") { app.pickWorkingDirectory() }
                    .keyboardShortcut("o", modifiers: [.command, .shift])
            }
        }

        MenuBarExtra {
            MenuContent(server: app.server, openKeys: { app.showKeys = true }) {
                app.shutdown(); NSApplication.shared.terminate(nil)
            }
        } label: {
            MenuBarStatusLabel(poller: app.statusPoller)
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
        .sheet(isPresented: $app.showKeys) {
            KeysView(port: app.server.port)
        }
    }
}

struct MenuContent: View {
    @ObservedObject var server: ServerManager
    var openKeys: () -> Void = {}
    let quit: () -> Void

    var body: some View {
        Text(server.state == .ready ? "● Services running" : "Starting…")
        if let u = server.updateAvailable {
            Button("Update available — install") { server.applyUpdate() }
            Text(u).font(.caption)
        }
        Divider()
        Button("API Keys…") { openKeys() }
        Button("Restart services") { server.restart() }
        Button("Quit Scroll") { quit() }
    }
}
