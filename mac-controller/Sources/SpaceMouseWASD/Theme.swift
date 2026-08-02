import SwiftUI

/// The dark palette, matching the Python controller's hex values exactly.
enum Theme {
    static let bg = Color(hex: 0x10131A)
    static let card = Color(hex: 0x181D26)
    static let field = Color(hex: 0x222938)
    static let fieldHi = Color(hex: 0x2B3344)
    static let accent = Color(hex: 0x38E1C8)
    static let text = Color(hex: 0xE8ECF1)
    static let dim = Color(hex: 0x8A93A3)
    static let okGreen = Color(hex: 0x48D17C)
    static let waitAmber = Color(hex: 0xF0B45A)
    static let danger = Color(hex: 0xF56565)
    static let ringOuter = Color(hex: 0x2EC9B4)
    static let versionGray = Color(hex: 0x3D4452)

    static let ui = "Helvetica Neue"
    static let mono = "Menlo"
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: 1.0)
    }
}

/// The orbit-ring logo, drawn to match `draw_logo` in the Python app.
struct LogoView: View {
    var size: CGFloat

    var body: some View {
        Canvas { ctx, _ in
            let s = size / 256.0
            func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
                CGPoint(x: x * s, y: y * s)
            }
            func rect(_ a: CGFloat, _ b: CGFloat, _ c: CGFloat,
                      _ d: CGFloat) -> CGRect {
                CGRect(x: a * s, y: b * s, width: (c - a) * s, height: (d - b) * s)
            }

            ctx.stroke(Path(ellipseIn: rect(44, 44, 212, 212)),
                       with: .color(Theme.ringOuter),
                       lineWidth: max(10 * s, 2))
            ctx.fill(Path(ellipseIn: rect(98, 98, 158, 158)),
                     with: .color(Theme.accent))

            let chev = max(10 * s, 2)
            let chevrons: [[CGFloat]] = [
                [114, 72, 128, 54, 142, 72], [114, 184, 128, 202, 142, 184],
                [72, 114, 54, 128, 72, 142], [184, 114, 202, 128, 184, 142],
            ]
            for c in chevrons {
                var path = Path()
                path.move(to: p(c[0], c[1]))
                path.addLine(to: p(c[2], c[3]))
                path.addLine(to: p(c[4], c[5]))
                ctx.stroke(path, with: .color(Theme.text),
                           style: StrokeStyle(lineWidth: chev, lineCap: .round,
                                              lineJoin: .round))
            }
        }
        .frame(width: size, height: size)
    }
}
