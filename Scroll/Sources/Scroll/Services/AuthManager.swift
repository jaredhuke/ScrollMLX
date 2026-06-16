import Foundation
import LocalAuthentication

/// Passkey access, built in from the ground up.
///
/// Local app unlock + power-action re-auth use LocalAuthentication (Touch ID /
/// device password / the platform passkey), which is the correct macOS primitive
/// for *this device*. Cross-device WebAuthn passkeys (AuthenticationServices) need
/// a relying-party server + challenge — that gets wired to the cloud-sync identity
/// when the sync backend lands (see CloudSync). Until then, `unlock` and `gate`
/// are fully functional.
@MainActor
final class AuthManager: ObservableObject {
    @Published private(set) var unlocked = false
    @Published var requireUnlock = true   // user can disable in Settings

    /// Unlock the app. Call on launch; gate the UI behind `unlocked`.
    func unlock() async {
        guard requireUnlock else { unlocked = true; return }
        unlocked = await evaluate(reason: "Unlock Scroll")
    }

    /// Re-authenticate before a power action (use an API key, deploy, rotate a key, push to git).
    @discardableResult
    func gate(_ reason: String) async -> Bool {
        await evaluate(reason: reason)
    }

    var biometryName: String {
        let ctx = LAContext()
        _ = ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
        switch ctx.biometryType {
        case .touchID: return "Touch ID"
        case .opticID: return "Optic ID"
        default: return "your passkey"
        }
    }

    private func evaluate(reason: String) async -> Bool {
        let ctx = LAContext()
        ctx.localizedFallbackTitle = "Use password"
        var err: NSError?
        let policy: LAPolicy = ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &err)
            ? .deviceOwnerAuthenticationWithBiometrics
            : .deviceOwnerAuthentication
        return await withCheckedContinuation { cont in
            ctx.evaluatePolicy(policy, localizedReason: reason) { ok, _ in
                DispatchQueue.main.async { cont.resume(returning: ok) }
            }
        }
    }
}
