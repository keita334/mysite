import AppKit
import SwiftUI

/// 音に合わせて粒子が動くビジュアル表示。ダブルクリックでフルスクリーン
struct ParticleVisualizerView: View {
    let analyzer: SpectrumAnalyzer

    /// 粒子の状態は描画のたびに書き換えるだけで、画面の更新は TimelineView が毎フレーム行う
    @State private var system = ParticleSystem()

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { context, size in
                // analyzer は監視せず、毎フレーム最新の値を読みに行く
                system.update(time: timeline.date.timeIntervalSinceReferenceDate, size: size,
                              levels: analyzer.levels, energy: analyzer.energy)
                system.draw(in: &context, size: size)
            } symbols: {
                ParticleSystem.glowSymbols
            }
        }
        .background(Color.black)
        .contentShape(Rectangle())
        .onTapGesture(count: 2) {
            NSApp.keyWindow?.toggleFullScreen(nil)
        }
        .help("ダブルクリックでフルスクリーン")
        .onAppear { analyzer.isVisualizerActive = true }
        .onDisappear { analyzer.isVisualizerActive = false }
    }
}

/// 粒子の動きと描画
///
/// - 円周上の粒子: 画面上の角度ごとに担当する周波数が決まっていて (上が低音、下が高音、左右対称)、
///   その帯域が鳴るほど外へ押し出され、明るく大きくなる。粒子自体は中域の量に応じた速さで円周を流れる
/// - 低音の拍 (キックなど) を見つけると、全体が脈打ち、火花が外へ飛び散る
/// - 高音が多いほど粒子が細かく震える
final class ParticleSystem {
    private struct Dot {
        /// 上を 0 として時計回り (rad)
        var angle: Double
        /// 回転速度の個体差
        var drift: Double
        /// 0 = 内側の層 … 1 = 外側の層
        var shell: Double
        var phase: Double
        var size: Double
    }

    private struct Spark {
        var position: CGPoint
        var velocity: CGVector
        var age = 0.0
        var life: Double
        var color: Int
        var size: Double
    }

    static let dotCount = 540
    static let maxSparks = 500
    /// 帯域の 8 色の間を補間した、低音→高音の色
    static let palette: [Color] = {
        let rgb = Theme.bandRGB
        let steps = 24
        return (0..<steps).map { i in
            let x = Double(i) / Double(steps - 1) * Double(rgb.count - 1)
            let k = min(Int(x), rgb.count - 2), f = x - Double(k)
            return Color(red: rgb[k].red + (rgb[k + 1].red - rgb[k].red) * f,
                         green: rgb[k].green + (rgb[k + 1].green - rgb[k].green) * f,
                         blue: rgb[k].blue + (rgb[k + 1].blue - rgb[k].blue) * f)
        }
    }()

    /// 色ごとの光の粒。一度だけ描いておき、毎フレームはそれを置くだけにする (粒子ごとにグラデーションを描くより軽い)
    @ViewBuilder static var glowSymbols: some View {
        ForEach(palette.indices, id: \.self) { index in
            Circle()
                .fill(RadialGradient(stops: [
                    .init(color: .white.opacity(0.95), location: 0),
                    .init(color: palette[index], location: 0.18),
                    .init(color: palette[index].opacity(0.3), location: 0.45),
                    .init(color: palette[index].opacity(0), location: 1),
                ], center: .center, startRadius: 0, endRadius: 16))
                .frame(width: 32, height: 32)
                .tag(index)
        }
    }

    private var dots: [Dot]
    private var sparks: [Spark] = []
    /// 表示点ごとの 0...1 に正規化したレベル (なめらかに追従)
    private var profile = [Double](repeating: 0, count: SpectrumAnalyzer.pointCount)
    private var reference = -70.0
    private var bassAverage = -90.0
    private var lastBeat = -1.0
    private var pulse = 0.0
    private var loudness = 0.0
    private var highLevel = 0.0
    private var rotation = 0.0
    private var time = 0.0
    private var lastTime: Double?

    init() {
        dots = (0..<Self.dotCount).map { i in
            Dot(angle: Double(i) / Double(Self.dotCount) * 2 * .pi,
                drift: .random(in: -0.3...0.3),
                shell: .random(in: 0...1),
                phase: .random(in: 0...(2 * .pi)),
                size: .random(in: 1.2...2.6))
        }
    }

    // MARK: - 更新

    func update(time now: Double, size: CGSize, levels: [Float], energy: SpectrumAnalyzer.Energy) {
        // 非表示から戻ったときなどに大きく飛ばないよう、1 フレームは最大 1/20 秒として扱う
        let dt = min(max(now - (lastTime ?? now), 0), 1.0 / 20)
        lastTime = now
        time += dt

        updateProfile(levels: levels, dt: dt)

        // 低音の拍: 直近 0.4 秒ほどの平均より急に大きくなったら拍とみなす
        let bass = Double(energy.bass)
        let rise = bass - bassAverage
        // 無音から鳴り始めたときに平均の追従が遅れて拍を連発しないよう、大きく下回っていたら一気に寄せる
        bassAverage += (bass - bassAverage) * (rise > 15 ? 0.5 : min(dt * 2.5, 1))
        if rise > 4, bass > -45, time - lastBeat > 0.2 {
            lastBeat = time
            let strength = min(rise / 10, 1)
            pulse = min(pulse + strength, 1.5)
            emitSparks(count: Int(20 + 70 * strength), strength: strength, size: size)
        }
        pulse *= exp(-dt * 5)

        let targetLoudness = ((Double(energy.overall) + 50) / 40).clamped(to: 0...1)
        loudness += (targetLoudness - loudness) * min(dt * 8, 1)
        // 高域の量は全体との比で見る (音量によらず「シャリシャリ感」で震えるように)
        let highRatio = energy.overall > -80 ? ((Double(energy.high) - Double(energy.overall) + 30) / 25).clamped(to: 0...1) : 0
        highLevel += (highRatio - highLevel) * min(dt * 10, 1)
        let mid = ((Double(energy.mid) + 50) / 40).clamped(to: 0...1)
        rotation += dt * (0.06 + mid * 0.4)

        let drag = exp(-dt * 1.6)
        for i in sparks.indices {
            sparks[i].age += dt
            sparks[i].velocity.dx *= drag
            sparks[i].velocity.dy *= drag
            sparks[i].position.x += sparks[i].velocity.dx * dt
            sparks[i].position.y += sparks[i].velocity.dy * dt
        }
        sparks.removeAll { $0.age >= $0.life }
    }

    /// スペクトルを 0...1 にする。音量や曲の帯域バランスが変わっても動きが出るよう、基準を自動で動かす
    private func updateProfile(levels: [Float], dt: Double) {
        guard levels.count == profile.count else { return }
        var compensated = [Double](repeating: 0, count: levels.count)
        var peak = -200.0
        for i in levels.indices {
            // 音楽は高域ほどレベルが下がるので、1 kHz より上は 1 オクターブごとに 3 dB 持ち上げて見る
            let octavesAbove1k = max(log2(SpectrumAnalyzer.frequencies[i] / 1000), 0)
            compensated[i] = Double(levels[i]) + octavesAbove1k * 3
            peak = max(peak, compensated[i])
        }
        // 大きな音にはすぐ合わせ、静かになったらゆっくり下げる。-70 dB より下げないのは無音のノイズを拡大しないため
        reference = peak > reference ? peak : max(reference - 6 * dt, -70)
        let range = 45.0
        for i in profile.indices {
            let target = ((compensated[i] - (reference - range)) / range).clamped(to: 0...1)
            let rate = target > profile[i] ? 0.5 : 0.1
            profile[i] += (target - profile[i]) * min(rate * dt * 60, 1)
        }
    }

    private func emitSparks(count: Int, strength: Double, size: CGSize) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let radius = Self.baseRadius(size)
        for _ in 0..<max(min(count, Self.maxSparks - sparks.count), 0) {
            // よく鳴っている帯域の位置から多く飛ばす
            var angle = Double.random(in: 0..<(2 * .pi))
            for _ in 0..<3 where Double.random(in: 0...1) > level(at: frequencyPosition(angle)) + 0.15 {
                angle = Double.random(in: 0..<(2 * .pi))
            }
            let u = frequencyPosition(angle)
            let direction = CGVector(dx: sin(angle), dy: -cos(angle))
            let r = radius * (1 + level(at: u) * 0.5)
            let speed = Double.random(in: 120...420) * (0.6 + strength)
            let swirl = Double.random(in: -60...60)
            sparks.append(Spark(
                position: CGPoint(x: center.x + direction.dx * r, y: center.y + direction.dy * r),
                velocity: CGVector(dx: direction.dx * speed - direction.dy * swirl,
                                   dy: direction.dy * speed + direction.dx * swirl),
                life: .random(in: 0.8...1.8),
                color: Self.colorIndex(u),
                size: .random(in: 1.5...3.5)))
        }
    }

    // MARK: - 描画

    func draw(in context: inout GraphicsContext, size: CGSize) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let radius = Self.baseRadius(size)
        let symbols = Self.palette.indices.compactMap { context.resolveSymbol(id: $0) }
        guard symbols.count == Self.palette.count else { return }

        // 背景: 音量に合わせて中心がぼんやり光る
        let glow = radius * (1.8 + pulse * 0.4)
        context.fill(Path(ellipseIn: CGRect(x: center.x - glow, y: center.y - glow, width: glow * 2, height: glow * 2)),
                     with: .radialGradient(Gradient(colors: [Theme.accent.opacity(0.06 + loudness * 0.16), .clear]),
                                           center: center, startRadius: 0, endRadius: glow))

        // 重なった光が明るくなるように加算で描く
        context.blendMode = .plusLighter

        // 中心のコア: 拍で膨らむ
        let core = radius * (0.35 + pulse * 0.25)
        context.opacity = 0.12 + pulse * 0.35 + loudness * 0.1
        context.draw(symbols[0], in: CGRect(x: center.x - core, y: center.y - core, width: core * 2, height: core * 2))

        for dot in dots {
            let theta = dot.angle + rotation * (1 + dot.drift)
            let u = frequencyPosition(theta)
            let v = level(at: u)
            let wobble = sin(time * (1.2 + dot.shell * 2) + dot.phase) * 3
            let jitter = Double.random(in: -1...1) * highLevel * 5
            let r = radius * (0.9 + dot.shell * 0.2) * (1 + pulse * 0.12)
                + v * radius * (0.2 + dot.shell * 0.7)
                + wobble + jitter
            let point = CGPoint(x: center.x + sin(theta) * r, y: center.y - cos(theta) * r)
            let side = dot.size * (1 + v * 1.6) * 6
            context.opacity = 0.18 + 0.82 * v * (0.5 + 0.5 * dot.shell)
            context.draw(symbols[Self.colorIndex(u)],
                         in: CGRect(x: point.x - side / 2, y: point.y - side / 2, width: side, height: side))
        }

        for spark in sparks {
            let t = spark.age / spark.life
            let fade = (1 - t) * (1 - t)
            let side = spark.size * (1 - t * 0.5) * 6
            // 動きの向きに短い尾を引く
            var tail = Path()
            tail.move(to: spark.position)
            tail.addLine(to: CGPoint(x: spark.position.x - spark.velocity.dx * 0.05,
                                     y: spark.position.y - spark.velocity.dy * 0.05))
            context.opacity = fade * 0.6
            context.stroke(tail, with: .color(Self.palette[spark.color]), lineWidth: spark.size * 0.7)
            context.opacity = fade
            context.draw(symbols[spark.color],
                         in: CGRect(x: spark.position.x - side / 2, y: spark.position.y - side / 2, width: side, height: side))
        }
    }

    // MARK: - 補助

    private static func baseRadius(_ size: CGSize) -> Double {
        min(size.width, size.height) * 0.24
    }

    /// 画面上の角度 → 担当する周波数の位置 (0 = 20 Hz … 1 = 20 kHz)。上が低音、下が高音で左右対称
    private func frequencyPosition(_ angle: Double) -> Double {
        var a = angle.truncatingRemainder(dividingBy: 2 * .pi)
        if a < 0 { a += 2 * .pi }
        return a <= .pi ? a / .pi : (2 * .pi - a) / .pi
    }

    private func level(at u: Double) -> Double {
        let x = u.clamped(to: 0...1) * Double(profile.count - 1)
        let i = min(Int(x), profile.count - 2), f = x - Double(i)
        return profile[i] * (1 - f) + profile[i + 1] * f
    }

    private static func colorIndex(_ u: Double) -> Int {
        min(Int(u.clamped(to: 0...1) * Double(palette.count)), palette.count - 1)
    }
}
