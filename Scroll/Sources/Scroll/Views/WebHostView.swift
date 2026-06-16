import SwiftUI
import WebKit
import AppKit

/// Hosts the HTML UI (served at 127.0.0.1) and bridges native superpowers to it:
/// fresh context, secure Keychain rotation (native prompt — keys never enter the web
/// layer), and the human security gate. The web calls:
///   await window.webkit.messageHandlers.native.postMessage({action:"…", …})
struct WebHostView: NSViewRepresentable {
    let port: Int
    let context: ContextEngine

    func makeCoordinator() -> Coordinator { Coordinator(port: port, context: context) }

    func makeNSView(context ctx: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = true
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(
            source: "window.__nativeShell=true;",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.addScriptMessageHandler(ctx.coordinator, contentWorld: .page, name: "native")
        cfg.userContentController = ucc

        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
        ctx.coordinator.webView = wv
        return wv
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, WKScriptMessageHandlerWithReply {
        let port: Int
        let context: ContextEngine
        weak var webView: WKWebView?

        init(port: Int, context: ContextEngine) {
            self.port = port; self.context = context
        }

        func userContentController(_ ucc: WKUserContentController,
                                   didReceive message: WKScriptMessage,
                                   replyHandler: @escaping (Any?, String?) -> Void) {
            guard let body = message.body as? [String: Any],
                  let action = body["action"] as? String else {
                replyHandler(nil, "bad message"); return
            }
            switch action {
            case "gatherContext":
                Task { _ = await context.gatherNow(port: port); replyHandler(["ok": true], nil) }
            case "keyStatus":
                replyHandler(Keychain.status(), nil)
            case "rotateKey":
                let provider = (body["provider"] as? String) ?? ""
                Task { replyHandler(["ok": await self.rotateKey(provider)], nil) }
            case "confirm":
                let a = (body["title"] as? String) ?? "this action"
                let d = (body["detail"] as? String) ?? ""
                Task { replyHandler(["allow": await SecurityGate.confirm(action: a, detail: d)], nil) }
            case "reveal":
                if let p = body["path"] as? String {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)])
                }
                replyHandler(["ok": true], nil)
            default:
                replyHandler(nil, "unknown action")
            }
        }

        /// Native secure prompt → Keychain. The secret never touches the web layer.
        private func rotateKey(_ provider: String) async -> Bool {
            await withCheckedContinuation { cont in
                let alert = NSAlert()
                alert.messageText = "Set \(provider) API key"
                alert.informativeText = "Stored in your Keychain — never sent to the web view."
                let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
                alert.accessoryView = field
                alert.addButton(withTitle: "Save")
                alert.addButton(withTitle: "Cancel")
                let r = alert.runModal()
                if r == .alertFirstButtonReturn, !field.stringValue.isEmpty {
                    cont.resume(returning: Keychain.set(provider, field.stringValue))
                } else {
                    cont.resume(returning: false)
                }
            }
        }
    }
}
