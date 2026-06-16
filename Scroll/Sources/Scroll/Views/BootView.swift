import SwiftUI

/// One-click boot screen: shows each startup step, errors with a retry, and an
/// "update available" affordance. Calm cream/teal styling to match the web UI.
struct BootView: View {
    @ObservedObject var server: ServerManager

    private let cream = Color(red: 0.94, green: 0.925, blue: 0.894)
    private let ink = Color(red: 0.10, green: 0.11, blue: 0.13)
    private let teal = Color(red: 0.184, green: 0.612, blue: 0.545)
    private let red = Color(red: 0.824, green: 0.333, blue: 0.18)

    var body: some View {
        ZStack {
            cream.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("MLX STUDIO").font(.system(size: 13, weight: .semibold)).tracking(4).foregroundStyle(ink)
                    Text("Bringing your local services online").font(.system(size: 11)).foregroundStyle(ink.opacity(0.45))
                }

                VStack(alignment: .leading, spacing: 13) {
                    ForEach(server.steps) { step in stepRow(step) }
                }

                if case let .error(msg) = server.state {
                    VStack(alignment: .leading, spacing: 9) {
                        Text(msg).font(.system(size: 12)).foregroundStyle(red).fixedSize(horizontal: false, vertical: true)
                        Button("Retry") { server.restart() }.buttonStyle(.borderedProminent).tint(teal)
                    }.padding(.top, 4)
                }

                if let upd = server.updateAvailable {
                    HStack(spacing: 10) {
                        Text("Update available — \(upd)").font(.system(size: 11)).foregroundStyle(ink.opacity(0.6))
                        Button("Update & restart") { server.applyUpdate() }.controlSize(.small).tint(teal)
                    }
                }
            }
            .frame(width: 380, alignment: .leading)
            .padding(40)
        }
    }

    @ViewBuilder private func stepRow(_ step: BootStep) -> some View {
        HStack(spacing: 11) {
            switch step.state {
            case .pending: Circle().strokeBorder(ink.opacity(0.15), lineWidth: 1.5).frame(width: 14, height: 14)
            case .running: ProgressView().controlSize(.small).frame(width: 14, height: 14)
            case .done:    Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(teal).frame(width: 14)
            case .failed:  Image(systemName: "xmark").font(.system(size: 11, weight: .bold)).foregroundStyle(red).frame(width: 14)
            }
            Text(step.label)
                .font(.system(size: 13, weight: step.state == .running ? .medium : .regular))
                .foregroundStyle(step.state == .done ? ink.opacity(0.55) : (step.state == .running ? ink : ink.opacity(0.4)))
            Spacer()
        }
    }
}
