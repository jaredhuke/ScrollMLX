import Foundation
import Combine

/// Polls the local server's /v1/status for the menu-bar thin-line widget:
/// a recent token-burn sparkline + session totals.
@MainActor
final class StatusPoller: ObservableObject {
    @Published var spark: [Double] = []
    @Published var tokens: Int = 0
    @Published var prompts: Int = 0
    @Published var ready: Bool = false

    private var timer: Timer?
    private var port: Int = 8080

    func start(port: Int) {
        self.port = port
        stop()
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
    }

    func stop() { timer?.invalidate(); timer = nil }

    private func poll() {
        guard let url = URL(string: "http://127.0.0.1:\(port)/v1/status") else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let data,
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
            let spark = (obj["spark"] as? [Any])?.compactMap { ($0 as? NSNumber)?.doubleValue } ?? []
            let tokens = (obj["total_tokens"] as? Int) ?? 0
            let prompts = (obj["prompts"] as? Int) ?? 0
            let ready = (obj["ready"] as? Bool) ?? false
            DispatchQueue.main.async {
                self?.spark = spark; self?.tokens = tokens; self?.prompts = prompts; self?.ready = ready
            }
        }.resume()
    }
}
