import SwiftUI

/// イコライザー用の縦型スライダー。0 dB を基準に上下へバーが伸びる
/// ダブルクリックで 0 にリセット
struct VerticalSlider: View {
    @Binding var value: Float
    var range: ClosedRange<Float>
    var step: Float = 0.5

    private let knobSize: CGFloat = 16
    private let trackWidth: CGFloat = 4

    var body: some View {
        GeometryReader { geo in
            let usable = max(geo.size.height - knobSize, 1)
            let knobY = yPosition(for: value, usable: usable)
            let zeroY = yPosition(for: min(max(0, range.lowerBound), range.upperBound), usable: usable)

            ZStack(alignment: .top) {
                Capsule()
                    .fill(.quaternary)
                    .frame(width: trackWidth)
                    .padding(.vertical, knobSize / 2)

                Rectangle()
                    .fill(Color.accentColor)
                    .frame(width: trackWidth, height: abs(zeroY - knobY))
                    .offset(y: min(zeroY, knobY))

                Circle()
                    .fill(.white)
                    .overlay(Circle().stroke(Color.black.opacity(0.15)))
                    .shadow(color: .black.opacity(0.25), radius: 1.5, y: 1)
                    .frame(width: knobSize, height: knobSize)
                    .offset(y: knobY - knobSize / 2)
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { drag in
                        value = valueFor(y: drag.location.y, usable: usable)
                    }
            )
            .simultaneousGesture(
                TapGesture(count: 2).onEnded { value = 0 }
            )
        }
    }

    private var span: Float { range.upperBound - range.lowerBound }

    /// 値 → ノブ中心の y 座標
    private func yPosition(for v: Float, usable: CGFloat) -> CGFloat {
        let ratio = CGFloat((v - range.lowerBound) / span)
        return knobSize / 2 + usable * (1 - ratio)
    }

    /// y 座標 → 値 (step 単位に丸める)
    private func valueFor(y: CGFloat, usable: CGFloat) -> Float {
        let ratio = 1 - (y - knobSize / 2) / usable
        let clamped = Float(min(max(ratio, 0), 1))
        let raw = range.lowerBound + clamped * span
        return (raw / step).rounded() * step
    }
}
