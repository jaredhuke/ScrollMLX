import Foundation

/// A Project is a workspace holding one or more repos. Each repo can live in
/// several locations (local disk, a git remote, iCloud, Google Drive), and the
/// sync DIRECTION is configured per location — "sync direction by location".
struct Project: Identifiable, Codable, Hashable {
    var id = UUID()
    var name: String
    var note: String = ""
    var repos: [Repo] = []
    var createdAt: Date = Date()
}

struct Repo: Identifiable, Codable, Hashable {
    var id = UUID()
    var name: String
    var localPath: String? = nil      // cloned-into or linked path on disk
    var remoteURL: String? = nil      // git remote (for clone / push / pull)
    var branch: String = ""
    var dirty: Int = 0                // count of uncommitted changes
    var lastCommit: String = ""
    var sync: [SyncTarget] = []       // one per location the repo is mirrored to
}

/// Where a repo is mirrored, and which way it syncs.
struct SyncTarget: Identifiable, Codable, Hashable {
    var id = UUID()
    var location: SyncLocation
    var direction: SyncDirection
    var enabled: Bool = true
    var lastSync: Date? = nil
}

enum SyncLocation: String, Codable, CaseIterable, Identifiable {
    case local, gitRemote, iCloud, googleDrive
    var id: String { rawValue }
    var label: String {
        switch self {
        case .local: return "Local disk"
        case .gitRemote: return "Git remote"
        case .iCloud: return "iCloud"
        case .googleDrive: return "Google Drive"
        }
    }
}

/// Direction of sync for a given location.
enum SyncDirection: String, Codable, CaseIterable, Identifiable {
    case pull        // location → here (e.g. clone/pull from git, restore from cloud)
    case push        // here → location (e.g. push to git, upload to cloud)
    case backup      // here → location, additive snapshots (cloud backup)
    case restore     // location → here, recover a lost/deleted artifact
    case mirror      // two-way keep-in-sync
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}
