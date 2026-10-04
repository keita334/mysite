import SwiftUI

/// 現在の EQ 設定の周波数特性カーブを描画する
struct EQCurveView: View {
    @EnvironmentObject private var audio: AudioEngine

    private let minFrequency = 20.0
    private let maxFrequency = 20_000.0
    private let dbRange = 15.0
    private let gridFrequencies: [(Double, String)] = [
        (32, "32"), (64, "64"), (125, "125"), (250, "250"), (500, "500"),
        (1000, "1k"), (2000, "2k"), (4000, "4k"), (8000, "8k"), (16000, "16k"),
    ]

    var body: some View {
        Canvas { context, size in
            func x(_ f: Double) -> CGFloat {
                CGFloat(log10(f / minFrequency) / log10(maxFrequency / minFrequency)) * size.width
            }
            func y(_ db: Double) -> CGFloat {
                let clamped = min(max(db, -dbRange), dbRange)
                return size.height / 2 - CGFloat(clamped / dbRange) * (size.height / 2)
            }

            // グリッド (dB)
            for db in [-12.0, -6, 0, 6, 12] {
                var line = Path()
                line.move(to: CGPoint(x: 0, y: y(db)))
                line.addLine(to: CGPoint(x: size.width, y: y(db)))
                context.stroke(line, with: .color(.secondary.opacity(db == 0 ? 0.5 : 0.2)), lineWidth: 1)
                context.draw(
                    Text(db > 0 ? "+\(Int(db))" : "\(Int(db))").font(.system(size: 9)).foregroundColor(.secondary),
                    at: CGPoint(x: 4, y: y(db) - 6), anchor: .leading
                )
            }

            // グリッド (周波数)
            for (f, label) in gridFrequencies {
                var line = Path()
                line.move(to: CGPoint(x: x(f), y: 0))
                line.addLine(to: CGPoint(x: x(f), y: size.height))
                context.stroke(line, with: .color(.secondary.opacity(0.15)), lineWidth: 1)
                context.draw(
                    Text(label).font(.system(size: 9)).foregroundColor(.secondary),
                    at: CGPoint(x: x(f), y: size.height - 2), anchor: .bottom
                )
            }

            // 周波数特性カーブ
            let steps = max(Int(size.width), 2)
            var curve = Path()
            for i in 0...steps {
                let t = Double(i) / Double(steps)
                let f = minFrequency * pow(maxFrequency / minFrequency, t)
                let point = CGPoint(x: CGFloat(t) * size.width, y: y(audio.response(atFrequency: f)))
                if i == 0 { curve.move(to: point) } else { curve.addLine(to: point) }
            }

            var fill = curve
            fill.addLine(to: CGPoint(x: size.width, y: y(0)))
            fill.addLine(to: CGPoint(x: 0, y: y(0)))
            fill.closeSubpath()

            let color: Color = audio.isBypassed ? .gray : .accentColor
            context.fill(fill, with: .color(color.opacity(0.18)))
            context.stroke(curve, with: .color(color), lineWidth: 2)
        }
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .textBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.3)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
