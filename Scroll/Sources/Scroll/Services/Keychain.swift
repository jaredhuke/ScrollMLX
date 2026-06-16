import Foundation
import Security

/// API-key storage in the login Keychain. Uses the SAME service name ("scroll")
/// as the Python CLI (`python cli.py key set …`), so keys set on either side are shared.
/// Keys are never written to disk in plaintext and never handed to the web layer.
enum Keychain {
    static let service = "scroll"
    static let providers = ["anthropic", "openai", "gemini"]

    @discardableResult
    static func set(_ provider: String, _ secret: String) -> Bool {
        let acct = provider.lowercased()
        delete(acct)
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: acct,
            kSecValueData as String: Data(secret.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }

    static func get(_ provider: String) -> String? {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider.lowercased(),
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(_ provider: String) {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider.lowercased(),
        ]
        SecItemDelete(q as CFDictionary)
    }

    /// provider -> has-key, plus a masked hint for display.
    static func status() -> [String: Bool] {
        Dictionary(uniqueKeysWithValues: providers.map { ($0, get($0) != nil) })
    }

    static func masked(_ provider: String) -> String {
        guard let k = get(provider), k.count > 6 else { return "—" }
        return "••••" + k.suffix(4)
    }
}
