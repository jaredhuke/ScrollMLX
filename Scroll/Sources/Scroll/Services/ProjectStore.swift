import Foundation

/// Owns projects + repos: persists them, clones/links git repos, refreshes status.
/// Cloning/network is a power action — callers should pass through AuthManager.gate first.
@MainActor
final class ProjectStore: ObservableObject {
    @Published private(set) var projects: [Project] = []

    private let storeURL: URL
    private let projectsRoot: URL   // ~/MLXProjects

    init() {
        let fm = FileManager.default
        let appSup = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MLXStudio", isDirectory: true)
        try? fm.createDirectory(at: appSup, withIntermediateDirectories: true)
        self.storeURL = appSup.appendingPathComponent("projects.json")

        self.projectsRoot = fm.homeDirectoryForCurrentUser.appendingPathComponent("MLXProjects", isDirectory: true)
        try? fm.createDirectory(at: projectsRoot, withIntermediateDirectories: true)

        load()
    }

    // MARK: - Persistence
    private func load() {
        guard let data = try? Data(contentsOf: storeURL),
              let decoded = try? JSONDecoder().decode([Project].self, from: data) else { return }
        projects = decoded
    }
    private func save() {
        guard let data = try? JSONEncoder().encode(projects) else { return }
        try? data.write(to: storeURL, options: .atomic)
    }

    // MARK: - Projects
    @discardableResult
    func addProject(name: String, note: String = "") -> Project {
        let p = Project(name: name, note: note)
        projects.append(p); save(); return p
    }
    func removeProject(_ id: Project.ID) { projects.removeAll { $0.id == id }; save() }

    // MARK: - Repos
    /// Link an existing local repo into a project.
    func linkRepo(into projectID: Project.ID, path: String) {
        guard let pi = projects.firstIndex(where: { $0.id == projectID }) else { return }
        var repo = Repo(name: URL(fileURLWithPath: path).lastPathComponent, localPath: path)
        repo.sync = [SyncTarget(location: .local, direction: .mirror)]
        refresh(&repo)
        projects[pi].repos.append(repo); save()
    }

    /// Clone a remote into the project's managed folder (power action).
    func cloneRepo(into projectID: Project.ID, remote: String) async {
        guard let pi = projects.firstIndex(where: { $0.id == projectID }) else { return }
        let proj = projects[pi]
        let name = Self.repoName(from: remote)
        let dest = projectsRoot
            .appendingPathComponent(proj.name, isDirectory: true)
            .appendingPathComponent(name, isDirectory: true)
        try? FileManager.default.createDirectory(at: dest.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        let ok = await Self.git(["clone", remote, dest.path]).ok
        guard ok else { return }
        var repo = Repo(name: name, localPath: dest.path, remoteURL: remote)
        repo.sync = [SyncTarget(location: .gitRemote, direction: .mirror),
                     SyncTarget(location: .local, direction: .mirror)]
        await MainActor.run {
            self.refresh(&repo)
            if let i = self.projects.firstIndex(where: { $0.id == projectID }) {
                self.projects[i].repos.append(repo); self.save()
            }
        }
    }

    func setSync(projectID: Project.ID, repoID: Repo.ID, location: SyncLocation, direction: SyncDirection, enabled: Bool) {
        guard let pi = projects.firstIndex(where: { $0.id == projectID }),
              let ri = projects[pi].repos.firstIndex(where: { $0.id == repoID }) else { return }
        if let si = projects[pi].repos[ri].sync.firstIndex(where: { $0.location == location }) {
            projects[pi].repos[ri].sync[si].direction = direction
            projects[pi].repos[ri].sync[si].enabled = enabled
        } else {
            projects[pi].repos[ri].sync.append(SyncTarget(location: location, direction: direction, enabled: enabled))
        }
        save()
    }

    func refreshAll() {
        for pi in projects.indices {
            for ri in projects[pi].repos.indices { refresh(&projects[pi].repos[ri]) }
        }
        save()
    }

    // MARK: - git helpers
    private func refresh(_ repo: inout Repo) {
        guard let path = repo.localPath,
              FileManager.default.fileExists(atPath: path + "/.git") else { return }
        repo.branch = Self.gitSync(["-C", path, "rev-parse", "--abbrev-ref", "HEAD"]).trimmed
        repo.dirty = Self.gitSync(["-C", path, "status", "--porcelain"]).split(separator: "\n").count
        repo.lastCommit = Self.gitSync(["-C", path, "log", "-1", "--pretty=%h %s"]).trimmed
    }

    static func repoName(from remote: String) -> String {
        var n = remote
        if let last = remote.split(separator: "/").last { n = String(last) }
        if n.hasSuffix(".git") { n.removeLast(4) }
        return n.isEmpty ? "repo" : n
    }

    private static func gitSync(_ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = args
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = Pipe()
        do { try p.run(); p.waitUntilExit() } catch { return "" }
        let d = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: d, encoding: .utf8) ?? ""
    }
    private static func git(_ args: [String]) async -> (ok: Bool, out: String) {
        await withCheckedContinuation { cont in
            DispatchQueue.global().async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
                p.arguments = args
                let pipe = Pipe(); p.standardOutput = pipe; p.standardError = Pipe()
                do { try p.run(); p.waitUntilExit() } catch { cont.resume(returning: (false, "")); return }
                let d = pipe.fileHandleForReading.readDataToEndOfFile()
                cont.resume(returning: (p.terminationStatus == 0, String(data: d, encoding: .utf8) ?? ""))
            }
        }
    }
}

private extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
