import SwiftUI

/// Native, secure place to add cloud API keys. The key is typed into a macOS
/// SecureField (never a web field), sent only to the local server on this Mac,
/// which stores it in the login Keychain and activates it live. Open with ⌘K.
struct KeysView: View {
    let port: Int
    @Environment(\.dismiss) private var dismiss

    struct Provider: Identifiable {
        let id: String, name: String, sub: String, link: String?
    }
    private let providers: [Provider] = [
        .init(id: "groq",       name: "Groq",       sub: "free · fast",        link: "https://console.groq.com/keys"),
        .init(id: "cerebras",   name: "Cerebras",   sub: "free · fastest",     link: "https://cloud.cerebras.ai"),
        .init(id: "openrouter", name: "OpenRouter", sub: "free open models",   link: "https://openrouter.ai/keys"),
        .init(id: "gemini",     name: "Gemini",     sub: "free tier · vision", link: "https://aistudio.google.com/apikey"),
        .init(id: "anthropic",  name: "Claude",     sub: "paid",               link: "https://console.anthropic.com/settings/keys"),
        .init(id: "openai",     name: "GPT-4o",     sub: "paid",               link: "https://platform.openai.com/api-keys"),
    ]

    @State private var status: [String: Bool] = [:]
    @State private var entry: [String: String] = [:]
    @State private var busy: String? = nil
    @State private var note: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("API keys").font(.system(size: 16, weight: .semibold))
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 20).padding(.top, 18).padding(.bottom, 6)

            Text("Stored in your macOS Keychain · stays on this Mac · never entered in the browser.")
                .font(.caption).foregroundStyle(.secondary)
                .padding(.horizontal, 20).padding(.bottom, 10)

            ScrollView {
                VStack(spacing: 14) {
                    ForEach(providers) { p in row(p) }
                }
                .padding(.horizontal, 20).padding(.vertical, 6)
            }

            if !note.isEmpty {
                Text(note).font(.caption).foregroundStyle(.secondary)
                    .padding(.horizontal, 20).padding(.bottom, 10)
            }
        }
        .frame(width: 440, height: 520)
        .onAppear(perform: refresh)
    }

    @ViewBuilder private func row(_ p: Provider) -> some View {
        let has = status[p.id] ?? false
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(has ? Color.green : Color.secondary.opacity(0.4)).frame(width: 7, height: 7)
                Text(p.name).font(.system(size: 13, weight: .medium))
                Text(p.sub).font(.caption2).foregroundStyle(.secondary)
                Spacer()
                if has {
                    Text("✓ set").font(.caption2).foregroundStyle(.green)
                    Button("Remove") { remove(p.id) }.font(.caption2).buttonStyle(.plain).foregroundStyle(.secondary)
                } else if let link = p.link, let url = URL(string: link) {
                    Link("Get a key ↗", destination: url).font(.caption2)
                }
            }
            HStack(spacing: 8) {
                SecureField(has ? "Replace key…" : "Paste \(p.name) key…", text: binding(p.id))
                    .textFieldStyle(.roundedBorder)
                    .disableAutocorrection(true)
                    .onSubmit { save(p.id) }
                Button(busy == p.id ? "Saving…" : "Save") { save(p.id) }
                    .disabled(busy == p.id || (entry[p.id] ?? "").count < 8)
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
    }

    private func binding(_ id: String) -> Binding<String> {
        Binding(get: { entry[id] ?? "" }, set: { entry[id] = $0 })
    }

    // MARK: - Server calls (localhost; the server stores to Keychain + activates live)

    private func refresh() {
        request("GET", "/v1/keys", nil) { obj in
            if let provs = obj?["providers"] as? [String: Any] {
                var s: [String: Bool] = [:]
                for (k, v) in provs { s[k] = (v as? Bool) ?? false }
                status = s
            }
        }
    }

    private func save(_ id: String) {
        let key = (entry[id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard key.count >= 8 else { note = "That key looks too short."; return }
        busy = id; note = ""
        request("POST", "/v1/keys/set", ["provider": id, "key": key]) { obj in
            busy = nil
            if (obj?["ok"] as? Bool) == true {
                entry[id] = ""; note = "\(id) connected — saved to your Keychain."; refresh()
            } else {
                note = "Could not save: \((obj?["error"] as? String) ?? "unknown")"
            }
        }
    }

    private func remove(_ id: String) {
        request("POST", "/v1/keys/delete", ["provider": id]) { _ in refresh() }
    }

    private func request(_ method: String, _ path: String, _ body: [String: Any]?,
                         _ done: @escaping ([String: Any]?) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = method
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        URLSession.shared.dataTask(with: req) { data, _, _ in
            let obj = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async { done(obj) }
        }.resume()
    }
}
