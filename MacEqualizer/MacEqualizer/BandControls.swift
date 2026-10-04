import SwiftUI

enum BandLayout {
    static let labelColumnWidth: CGFloat = 78
    static let outputColumnWidth: CGFloat = 78
    static let spacing: CGFloat = 6
}

enum ValueFormat {
    static func frequency(_ f: Double) -> String {
        if f >= 10_000 { return String(format: "%.1f kHz", f / 1000) }
        if f >= 1000 { return String(format: "%.2f kHz", f / 1000) }
        return String(format: f >= 100 ? "%.0f Hz" : "%.1f Hz", f)
    }

    static func gain(_ db: Double) -> String {
        db == 0 ? "0.0 dB" : String(format: "%+.1f dB", db)
    }

    static func q(_ q: Double) -> String {
        String(format: q >= 10 ? "%.1f" : "%.2f", q)
    }
}

// MARK: - バンドのオン/オフ

/// フィルタの形を表すアイコン
struct BandIcon: Shape {
    let type: EQBandType

    func path(in rect: CGRect) -> Path {
        // 左右対称なものは低域側の形だけ定義して反転する
        let mirrored = type == .highCut || type == .highShelf
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + (mirrored ? 1 - x : x) * rect.width, y: rect.minY + y * rect.height)
        }

        var path = Path()
        switch type {
        case .lowCut, .highCut:
            path.move(to: point(0.05, 0.95))
            path.addQuadCurve(to: point(0.5, 0.5), control: point(0.3, 0.5))
            path.addLine(to: point(0.95, 0.5))
        case .lowShelf, .highShelf:
            path.move(to: point(0.05, 0.15))
            path.addLine(to: point(0.3, 0.15))
            path.addCurve(to: point(0.7, 0.5), control1: point(0.5, 0.15), control2: point(0.5, 0.5))
            path.addLine(to: point(0.95, 0.5))
        case .peak:
            path.move(to: point(0.05, 0.6))
            path.addLine(to: point(0.25, 0.6))
            path.addCurve(to: point(0.5, 0.1), control1: point(0.38, 0.6), control2: point(0.4, 0.1))
            path.addCurve(to: point(0.75, 0.6), control1: point(0.6, 0.1), control2: point(0.62, 0.6))
            path.addLine(to: point(0.95, 0.6))
        }
        return path
    }
}

struct BandToggleButton: View {
    @Binding var band: EQBand
    let color: Color
    let isSelected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button {
            band.isOn.toggle()
            onSelect()
        } label: {
            BandIcon(type: band.type)
                .stroke(band.isOn ? Color.black.opacity(0.75) : Theme.label,
                        style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
                .frame(width: 30, height: 13)
                .frame(maxWidth: .infinity)
                .frame(height: 22)
                .background(RoundedRectangle(cornerRadius: 4).fill(band.isOn ? color : Theme.panel))
                .overlay(RoundedRectangle(cornerRadius: 4)
                    .stroke(isSelected ? Color.white.opacity(0.85) : Color.black.opacity(0.5), lineWidth: 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(band.isOn ? "クリックでこのバンドをオフ" : "クリックでこのバンドをオン")
    }
}

// MARK: - パラメーター

/// 上下にドラッグして値を変える数値欄 (Logic Pro と同じ操作)。ダブルクリックで初期値に戻す
struct DragValueField: View {
    let text: String
    var color: Color = Theme.text
    /// nil なら連続値 (ドラッグ量 px を渡す)、値があればその px ごとに ±1 を渡す
    var stepPixels: CGFloat?
    let onChange: (Double) -> Void
    var onReset: () -> Void = {}

    @State private var lastTranslation: CGFloat = 0
    @State private var accumulated: CGFloat = 0
    @State private var isDragging = false

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium).monospacedDigit())
            .foregroundColor(color)
            .lineLimit(1)
            .minimumScaleFactor(0.75)
            .frame(maxWidth: .infinity, minHeight: 20)
            .background(RoundedRectangle(cornerRadius: 3).fill(Color.black.opacity(isDragging ? 0.6 : 0.35)))
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onChanged { value in
                        isDragging = true
                        let delta = lastTranslation - value.translation.height
                        lastTranslation = value.translation.height
                        guard let stepPixels else {
                            onChange(Double(delta))
                            return
                        }
                        accumulated += delta
                        while accumulated >= stepPixels {
                            onChange(1)
                            accumulated -= stepPixels
                        }
                        while accumulated <= -stepPixels {
                            onChange(-1)
                            accumulated += stepPixels
                        }
                    }
                    .onEnded { _ in
                        lastTranslation = 0
                        accumulated = 0
                        isDragging = false
                    }
            )
            .onTapGesture(count: 2, perform: onReset)
            .help("上下にドラッグで調整 / ダブルクリックで初期値")
    }
}

struct BandParameterGrid: View {
    @EnvironmentObject private var eq: EqualizerModel
    @Binding var selectedBand: Int?

    var body: some View {
        HStack(alignment: .top, spacing: BandLayout.spacing) {
            VStack(alignment: .trailing, spacing: 4) {
                rowLabel("周波数")
                rowLabel("ゲイン/スロープ")
                rowLabel("Q")
            }
            .padding(.vertical, 5)
            .frame(width: BandLayout.labelColumnWidth, alignment: .trailing)

            ForEach(eq.bands.indices, id: \.self) { index in
                bandColumn(index)
            }

            VStack(spacing: 4) {
                Text("出力")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Theme.label)
                    .frame(height: 20)
                DragValueField(text: ValueFormat.gain(eq.outputGain)) { delta in
                    eq.outputGain = ((eq.outputGain + delta * 0.1) * 10).rounded() / 10
                    eq.outputGain = eq.outputGain.clamped(to: EqualizerModel.outputGainRange)
                } onReset: {
                    eq.outputGain = 0
                }
            }
            .padding(5)
            .background(RoundedRectangle(cornerRadius: 5).fill(Theme.panel))
            .frame(width: BandLayout.outputColumnWidth)
        }
    }

    private func rowLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .foregroundColor(Theme.label)
            .frame(height: 20)
    }

    private func bandColumn(_ index: Int) -> some View {
        let band = eq.bands[index]
        let color = Theme.bandColors[index]
        let isSelected = selectedBand == index
        let defaults = EQBand.defaults[index]

        return VStack(spacing: 4) {
            DragValueField(text: ValueFormat.frequency(band.frequency), color: color) { delta in
                edit(index) {
                    let f = $0.frequency * pow(2, delta / 60)
                    $0.frequency = EQGraphView.roundToSignificantDigits(f.clamped(to: EQBand.frequencyRange))
                }
            } onReset: {
                edit(index) { $0.frequency = defaults.frequency }
            }

            if band.type.hasGain {
                DragValueField(text: ValueFormat.gain(band.gain), color: color) { delta in
                    edit(index) { $0.gain = (($0.gain + delta * 0.1) * 10).rounded() / 10; $0.gain = $0.gain.clamped(to: EQBand.gainRange) }
                } onReset: {
                    edit(index) { $0.gain = 0 }
                }
                DragValueField(text: ValueFormat.q(band.q), color: color) { delta in
                    edit(index) { $0.q = (($0.q * pow(2, delta / 80)) * 100).rounded() / 100; $0.q = $0.q.clamped(to: EQBand.qRange) }
                } onReset: {
                    edit(index) { $0.q = defaults.q }
                }
            } else {
                DragValueField(text: "\(band.slope) dB/Oct", color: color, stepPixels: 12) { step in
                    edit(index) {
                        let current = EQBand.slopes.firstIndex(of: $0.slope) ?? 1
                        $0.slope = EQBand.slopes[(current + Int(step)).clamped(to: 0...(EQBand.slopes.count - 1))]
                    }
                } onReset: {
                    edit(index) { $0.slope = defaults.slope }
                }
                Text("—")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.label.opacity(0.5))
                    .frame(maxWidth: .infinity, minHeight: 20)
            }
        }
        .padding(5)
        .background(RoundedRectangle(cornerRadius: 5).fill(isSelected ? color.opacity(0.16) : Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(isSelected ? color.opacity(0.8) : Color.clear, lineWidth: 1))
        .opacity(band.isOn ? 1 : 0.45)
        .simultaneousGesture(TapGesture().onEnded { selectedBand = index })
    }

    /// 値を変えたバンドはオンにして選択する (Logic Pro と同じ)
    private func edit(_ index: Int, _ change: (inout EQBand) -> Void) {
        var band = eq.bands[index]
        change(&band)
        band.isOn = true
        eq.bands[index] = band
        selectedBand = index
    }
}
