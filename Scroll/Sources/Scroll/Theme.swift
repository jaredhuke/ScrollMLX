import SwiftUI

// Pen-and-ink on parchment. Hairline rules over shaded fills. Ink is the voice;
// teal is a rare breath; red marks only extremes. Motion is slow (ch'an).

enum Parchment {
    static let bg     = Color(red: 0.941, green: 0.925, blue: 0.894) // #F0ECE4
    static let bg2    = Color(red: 0.914, green: 0.894, blue: 0.859) // #E9E4DB
    static let bg3    = Color(red: 0.886, green: 0.863, blue: 0.824) // #E2DCD2
}

enum Ink {
    static let line   = Color(red: 0.102, green: 0.110, blue: 0.133) // #1A1C22 — primary
    static let mid    = Color(red: 0.290, green: 0.310, blue: 0.369)
    static let soft   = Color(red: 0.541, green: 0.565, blue: 0.612)
    static let ghost  = Color(red: 0.706, green: 0.729, blue: 0.776)
    static let red    = Color(red: 0.824, green: 0.333, blue: 0.180) // extremes only
    static let teal   = Color(red: 0.184, green: 0.612, blue: 0.545) // sparing
    static let rule       = Color.black.opacity(0.08)   // hairline
    static let ruleStrong = Color.black.opacity(0.14)
}

/// A thin pen line — use instead of shaded dividers/boxes.
struct Rule: View {
    var color: Color = Ink.rule
    var body: some View { Rectangle().fill(color).frame(height: 1) }
}
struct VRule: View {
    var color: Color = Ink.rule
    var body: some View { Rectangle().fill(color).frame(width: 1) }
}

/// Outline a region with a hairline instead of a fill.
struct InkBorder: ViewModifier {
    var color: Color = Ink.rule
    var radius: CGFloat = 8
    func body(content: Content) -> some View {
        content.overlay(RoundedRectangle(cornerRadius: radius).strokeBorder(color, lineWidth: 1))
    }
}
extension View {
    func inkBorder(_ color: Color = Ink.rule, radius: CGFloat = 8) -> some View {
        modifier(InkBorder(color: color, radius: radius))
    }
}

enum Lettering {
    /// Whisper-quiet section label (near-invisible, all caps, wide tracking).
    static func label(_ s: String) -> some View {
        Text(s.uppercased())
            .font(.system(size: 7, weight: .regular)).tracking(2.2)
            .foregroundStyle(Ink.ghost)
    }
    static let mono = Font.system(.body, design: .default) // ZERO monospace — humanist sans everywhere
}

/// Slow, meditative motion. No frantic loops.
enum Motion {
    static let calm   = Animation.easeInOut(duration: 0.9)
    static let breath = Animation.easeInOut(duration: 3.2).repeatForever(autoreverses: true)
    static let settle = Animation.easeOut(duration: 1.6)
}
