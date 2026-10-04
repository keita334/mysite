import SwiftUI

/// 周波数・ゲインと画面座標の対応
struct PlotGeometry {
    static let minFrequency = 20.0
    static let maxFrequency = 20_000.0
    static let gainRange = 24.0
    static let analyzerTop = 0.0
    static let analyzerBottom = -90.0
    static let labelStripHeight: CGFloat = 18
    /// ±24 dB の点がはみ出さないための上下の余白
    static let gainMargin: CGFloat = 10

    let size: CGSize

    var plotRect: CGRect {
        CGRect(x: 0, y: Self.labelStripHeight, width: size.width, height: max(size.height - Self.labelStripHeight, 1))
    }

    func x(_ frequency: Double) -> CGFloat {
        plotRect.minX + CGFloat(log(frequency / Self.minFrequency) / log(Self.maxFrequency / Self.minFrequency)) * plotRect.width
    }

    func frequency(atX x: CGFloat) -> Double {
        Self.minFrequency * pow(Self.maxFrequency / Self.minFrequency, Double((x - plotRect.minX) / plotRect.width))
    }

    private var gainHalfHeight: CGFloat { plotRect.height / 2 - Self.gainMargin }

    func y(gain db: Double) -> CGFloat {
        plotRect.midY - CGFloat(db / Self.gainRange) * gainHalfHeight
    }

    func gain(atY y: CGFloat) -> Double {
        Double((plotRect.midY - y) / gainHalfHeight) * Self.gainRange
    }

    func y(analyzer db: Double) -> CGFloat {
        plotRect.minY + CGFloat((Self.analyzerTop - db) / (Self.analyzerTop - Self.analyzerBottom)) * plotRect.height
    }
}

/// Logic Pro の Channel EQ 風のグラフ。背景にアナライザー、その上に各バンドと合成の特性カーブ
///
/// 点をドラッグすると周波数 (横) とゲイン (縦) が変わる。点をダブルクリックするとバンドのオン/オフ
struct EQGraphView: View {
    @EnvironmentObject private var eq: EqualizerModel
    @Binding var selectedBand: Int?

    @State private var hoveredBand: Int?
    @State private var dragState: DragState?

    private struct DragState {
        var band: Int
        var start: EQBand
    }

    private let sampleRate = EqualizerModel.displaySampleRate

    var body: some View {
        GeometryReader { geo in
            let plot = PlotGeometry(size: geo.size)
            ZStack {
                GraphGrid(plot: plot)
                SpectrumLayer(analyzer: eq.analyzer, plot: plot)
                GraphLabels(plot: plot)
                curveLayer(plot: plot)
            }
            .contentShape(Rectangle())
            .gesture(dragGesture(plot: plot))
            .simultaneousGesture(
                SpatialTapGesture(count: 2).onEnded { value in
                    guard let index = nearestBand(to: value.location, plot: plot, within: 14) else { return }
                    eq.bands[index].isOn.toggle()
                    selectedBand = index
                }
            )
            .onContinuousHover { phase in
                switch phase {
                case .active(let location):
                    hoveredBand = nearestBand(to: location, plot: plot, within: 16)
                case .ended:
                    hoveredBand = nil
                }
            }
        }
        .background(Theme.graphBackground)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.black.opacity(0.7)))
    }

    // MARK: - 描画

    private func curveLayer(plot: PlotGeometry) -> some View {
        let bands = eq.bands
        let sections = bands.map { $0.isOn ? $0.sections(sampleRate: sampleRate) : [] }
        let bypassed = eq.isBypassed
        let highlighted: Set<Int> = Set([selectedBand, hoveredBand].compactMap { $0 })
        let nodes = bands.indices.map { nodePosition(for: bands[$0], plot: plot) }
        let sampleRate = sampleRate

        return Canvas { context, _ in
            let rect = plot.plotRect
            context.clip(to: Path(rect))

            let xs = Array(stride(from: rect.minX, through: rect.maxX, by: 2))
            let frequencies = xs.map { plot.frequency(atX: $0) }
            let zeroY = plot.y(gain: 0)
            var total = [Double](repeating: 0, count: xs.count)

            // 各バンドの特性。選択中・ポインタを乗せているバンドだけ、そのバンドの色で塗って見せる
            for (index, bandSections) in sections.enumerated() where !bandSections.isEmpty {
                var path = Path()
                for (i, f) in frequencies.enumerated() {
                    let db = bandSections.reduce(0) { $0 + $1.magnitudeDB(at: f, sampleRate: sampleRate) }
                    total[i] += db
                    let point = CGPoint(x: xs[i], y: plot.y(gain: db))
                    if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
                }
                guard highlighted.contains(index) else { continue }
                let color = Theme.bandColors[index]
                var fill = path
                fill.addLine(to: CGPoint(x: rect.maxX, y: zeroY))
                fill.addLine(to: CGPoint(x: rect.minX, y: zeroY))
                fill.closeSubpath()
                context.fill(fill, with: .color(color.opacity(bypassed ? 0.1 : 0.28)))
            }

            // 合成の特性
            var curve = Path()
            for (i, x) in xs.enumerated() {
                let point = CGPoint(x: x, y: plot.y(gain: total[i]))
                if i == 0 { curve.move(to: point) } else { curve.addLine(to: point) }
            }
            context.stroke(curve, with: .color(bypassed ? Theme.label.opacity(0.6) : Theme.curve), lineWidth: 2)

            // 操作点
            for (index, band) in bands.enumerated() {
                let color = Theme.bandColors[index]
                let radius: CGFloat = index == selectedBand ? 6.5 : 5.5
                let center = nodes[index]
                let circle = CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
                if band.isOn {
                    context.fill(Path(ellipseIn: circle), with: .color(bypassed ? color.opacity(0.4) : color))
                } else {
                    context.stroke(Path(ellipseIn: circle), with: .color(color.opacity(0.55)), lineWidth: 1.5)
                }
                if index == selectedBand {
                    context.stroke(Path(ellipseIn: circle.insetBy(dx: -3, dy: -3)), with: .color(.white.opacity(0.9)), lineWidth: 1.5)
                }
            }
        }
        .allowsHitTesting(false)
    }

    /// 操作点の位置。カットは周波数での実際の減衰量 (-3 dB) の位置に置く
    private func nodePosition(for band: EQBand, plot: PlotGeometry) -> CGPoint {
        let x = plot.x(band.frequency)
        if band.type.hasGain {
            return CGPoint(x: x, y: plot.y(gain: band.gain))
        }
        let db = band.sections(sampleRate: sampleRate).reduce(0) { $0 + $1.magnitudeDB(at: band.frequency, sampleRate: sampleRate) }
        return CGPoint(x: x, y: plot.y(gain: db))
    }

    // MARK: - 操作

    private func nearestBand(to point: CGPoint, plot: PlotGeometry, within limit: CGFloat = .infinity) -> Int? {
        let distances = eq.bands.indices.map { index -> (Int, CGFloat) in
            let node = nodePosition(for: eq.bands[index], plot: plot)
            return (index, hypot(node.x - point.x, node.y - point.y))
        }
        guard let nearest = distances.min(by: { $0.1 < $1.1 }), nearest.1 <= limit else { return nil }
        return nearest.0
    }

    private func dragGesture(plot: PlotGeometry) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if dragState == nil {
                    guard let index = nearestBand(to: value.startLocation, plot: plot) else { return }
                    dragState = DragState(band: index, start: eq.bands[index])
                    selectedBand = index
                }
                // クリックしただけでは値を変えない (選択だけ)
                guard let state = dragState, value.translation != .zero else { return }

                var band = state.start
                let x = plot.x(state.start.frequency) + value.translation.width
                band.frequency = Self.roundToSignificantDigits(plot.frequency(atX: x).clamped(to: EQBand.frequencyRange))
                if band.type.hasGain {
                    let y = plot.y(gain: state.start.gain) + value.translation.height
                    band.gain = ((plot.gain(atY: y) * 10).rounded() / 10).clamped(to: EQBand.gainRange)
                }
                band.isOn = true
                eq.bands[state.band] = band
            }
            .onEnded { _ in
                dragState = nil
            }
    }

    /// 1234.5 Hz → 1230 Hz のように有効数字 3 桁に丸める
    static func roundToSignificantDigits(_ value: Double, digits: Int = 3) -> Double {
        let scale = pow(10, Double(digits) - ceil(log10(value)))
        return (value * scale).rounded() / scale
    }
}

/// 目盛り線
private struct GraphGrid: View {
    let plot: PlotGeometry

    var body: some View {
        Canvas { context, size in
            let rect = plot.plotRect
            context.fill(Path(CGRect(x: 0, y: 0, width: size.width, height: rect.minY)), with: .color(.black.opacity(0.3)))

            func line(from a: CGPoint, to b: CGPoint, color: Color) {
                var path = Path()
                path.move(to: a)
                path.addLine(to: b)
                context.stroke(path, with: .color(color), lineWidth: 1)
            }

            // 周波数 (1, 2, …, 9 × 10^n)
            for decade in [10.0, 100, 1000, 10_000] {
                for m in 1...9 {
                    let f = decade * Double(m)
                    guard f > PlotGeometry.minFrequency, f < PlotGeometry.maxFrequency else { continue }
                    let x = plot.x(f)
                    line(from: CGPoint(x: x, y: rect.minY), to: CGPoint(x: x, y: rect.maxY),
                         color: m == 1 ? Theme.gridMajor : Theme.gridMinor)
                }
            }

            // ゲイン (6 dB ごと)
            for db in stride(from: -24.0, through: 24.0, by: 6) {
                let y = plot.y(gain: db)
                let color = db == 0 ? Theme.zeroLine : (Int(db) % 12 == 0 ? Theme.gridMajor : Theme.gridMinor)
                line(from: CGPoint(x: rect.minX, y: y), to: CGPoint(x: rect.maxX, y: y), color: color)
            }
        }
        .allowsHitTesting(false)
    }
}

/// 軸ラベル。アナライザーに隠れないよう、その上に重ねる
private struct GraphLabels: View {
    let plot: PlotGeometry

    private static let frequencyLabels: [(Double, String)] = [
        (20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
        (1000, "1k"), (2000, "2k"), (5000, "5k"), (10_000, "10k"), (20_000, "20k"),
    ]

    var body: some View {
        Canvas { context, _ in
            let rect = plot.plotRect
            let font = Font.system(size: 10, weight: .medium).monospacedDigit()

            for (f, text) in Self.frequencyLabels {
                let x = plot.x(f)
                let anchor: UnitPoint = f == PlotGeometry.minFrequency ? .leading : (f == PlotGeometry.maxFrequency ? .trailing : .center)
                let offset: CGFloat = anchor == .leading ? 4 : (anchor == .trailing ? -4 : 0)
                context.draw(Text(text).font(font).foregroundColor(Theme.label),
                             at: CGPoint(x: x + offset, y: rect.minY / 2), anchor: anchor)
            }

            // 右: EQ のゲイン、左: アナライザーのレベル
            for db in [24, 12, 0, -12, -24] {
                let text = db > 0 ? "+\(db)" : "\(db)"
                context.draw(Text(text).font(font).foregroundColor(Theme.label),
                             at: CGPoint(x: rect.maxX - 5, y: plot.y(gain: Double(db)) - 7), anchor: .trailing)
            }
            for db in [-20, -40, -60, -80] {
                context.draw(Text("\(db)").font(font).foregroundColor(Theme.analyzerLabel),
                             at: CGPoint(x: rect.minX + 5, y: plot.y(analyzer: Double(db)) - 7), anchor: .leading)
            }
        }
        .allowsHitTesting(false)
    }
}

/// EQ 後の出力のスペクトル。アナライザーだけを監視して、ほかの部分を描き直さないようにする
private struct SpectrumLayer: View {
    @ObservedObject var analyzer: SpectrumAnalyzer
    let plot: PlotGeometry

    var body: some View {
        Canvas { context, _ in
            guard analyzer.isEnabled else { return }
            let rect = plot.plotRect
            var line = Path()
            for (i, f) in SpectrumAnalyzer.frequencies.enumerated() {
                let y = min(plot.y(analyzer: Double(analyzer.levels[i])), rect.maxY)
                let point = CGPoint(x: plot.x(f), y: y)
                if i == 0 { line.move(to: point) } else { line.addLine(to: point) }
            }
            var fill = line
            fill.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
            fill.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
            fill.closeSubpath()

            context.fill(fill, with: .linearGradient(
                Gradient(colors: [Theme.analyzerFillTop, Theme.analyzerFillBottom]),
                startPoint: CGPoint(x: 0, y: rect.minY), endPoint: CGPoint(x: 0, y: rect.maxY)))
            context.stroke(line, with: .color(Theme.analyzerLine), lineWidth: 1)
        }
        .allowsHitTesting(false)
    }
}
