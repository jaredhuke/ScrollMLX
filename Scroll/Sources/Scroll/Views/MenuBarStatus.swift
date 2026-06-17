import SwiftUI

/// Thin-line live status shown directly in the macOS menu bar: a token-burn
/// sparkline + the session token total. High-level, minimal, legible.
struct MenuBarStatusLabel: View {
    @ObservedObject var poller: StatusPoller

    var body: some View {
        HStack(spacing: 5) {
            Sparkline(values: poller.spark)
                .stroke(Color.primary.opacity(poller.ready ? 0.7 : 0.3), lineWidth: 1)
                .frame(width: 34, height: 12)
            Text(fmt(poller.tokens))
                .font(.system(size: 11)).monospacedDigit()
        }
    }

    private func fmt(_ n: Int) -> String {
        n >= 1000 ? String(format: "%.1fk", Double(n) / 1000) : "\(n)"
    }
}

/// A flat, single-stroke sparkline (no fill, no glow) — pen-and-ink.
struct Sparkline: Shape {
    var values: [Double]
    func path(in rect: CGRect) -> Path {
        var p = Path()
        guard values.count > 1 else {
            p.move(to: CGPoint(x: rect.minX, y: rect.midY))
            p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
            return p
        }
        let maxV = max(values.max() ?? 1, 1)
        let n = values.count
        for (i, v) in values.enumerated() {
            let x = rect.minX + CGFloat(i) / CGFloat(n - 1) * rect.width
            let y = rect.maxY - CGFloat(v / maxV) * rect.height
            if i == 0 { p.move(to: CGPoint(x: x, y: y)) } else { p.addLine(to: CGPoint(x: x, y: y)) }
        }
        return p
    }
}
