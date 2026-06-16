import AppKit

/// Human-in-the-loop gate for powerful actions (stand up servers, download/install,
/// network calls, destructive ops). The agent/critic can *propose*; only the human
/// approves. This is the real security boundary — not a self-judging agent.
@MainActor
enum SecurityGate {
    /// Categories that ALWAYS require explicit approval.
    static let gated = ["install", "download", "network", "delete", "shell", "rotate-keys"]

    /// Present a blocking confirmation. Returns true only on explicit approval.
    static func confirm(action: String, detail: String) async -> Bool {
        await withCheckedContinuation { cont in
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Allow: \(action)?"
            alert.informativeText = detail
            alert.addButton(withTitle: "Allow once")
            alert.addButton(withTitle: "Cancel")
            let resp = alert.runModal()
            cont.resume(returning: resp == .alertFirstButtonReturn)
        }
    }

    static func requiresApproval(_ category: String) -> Bool {
        gated.contains(category.lowercased())
    }
}
