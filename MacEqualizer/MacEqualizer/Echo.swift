import Foundation

/// エコーの設定
struct EchoSettings: Codable, Equatable {
    var isOn = false
    /// 繰り返しの間隔 (秒)
    var time = 0.35
    /// 次の繰り返しの大きさの割合 (0 = 1 回だけ … 0.9 = 長く続く)
    var feedback = 0.35
    /// エコーの大きさ (原音に対して。1 = 最初のエコーが原音と同じ大きさ)
    var mix = 0.3
    /// 繰り返しを左右交互に飛ばす
    var pingPong = false

    static let timeRange = 0.03...1.5
    /// 1 未満に抑えて、繰り返しが必ず減衰するようにする
    static let feedbackRange = 0.0...0.9

    static let presets: [(name: String, settings: EchoSettings)] = [
        ("スラップバック", EchoSettings(isOn: true, time: 0.11, feedback: 0.1, mix: 0.35)),
        ("標準", EchoSettings(isOn: true, time: 0.35, feedback: 0.35, mix: 0.3)),
        ("ロング", EchoSettings(isOn: true, time: 0.65, feedback: 0.55, mix: 0.3)),
        ("ピンポン", EchoSettings(isOn: true, time: 0.375, feedback: 0.45, mix: 0.35, pingPong: true)),
    ]
}

/// 実験的なエコー (ディレイ)。EQ の後、環境エフェクトの前に BiquadEQ.process から呼ぶ
///
/// - 繰り返しの経路にローパス (約 4.5 kHz) を入れ、繰り返すほど音がこもるようにする (アナログやテープのエコーに近い響き)
/// - 遅延時間を変えても急に飛ばず、なめらかに目標に近づく。その間は音程が揺れる (テープエコーの速度を変えたときと同じ)。
///   急に飛ばすと波形が途切れてプツッと鳴るため。動く速さは最大 0.5 サンプル/サンプル (音程 0.5〜1.5 倍) に抑える
final class EchoEffect {
    /// 遅延が 1 サンプルあたりに動ける最大量。これを超えると音程の変化が極端になる
    private static let maxDelaySlew: Float = 0.5

    /// 192 kHz で最大の遅延時間を入れても足りる長さ (2 の累乗にして剰余をビット演算にする)
    private static let capacity = 1 << 19

    private struct State {
        var writeIndex = 0
        /// 今の遅延 (サンプル、小数あり)。目標に向かってなめらかに動く
        var delay: Float = 1
        var lowLeft: Float = 0
        var lowRight: Float = 0
        // 以下はサンプルレートで決まる値。IO が止まっている間 (configure) にだけ変える
        var mask = 0
        var smoothing: Float = 0
        var lowpass: Float = 0
    }

    private let left: UnsafeMutablePointer<Float>
    private let right: UnsafeMutablePointer<Float>
    private let state: UnsafeMutablePointer<State>

    init() {
        left = .allocate(capacity: Self.capacity)
        left.initialize(repeating: 0, count: Self.capacity)
        right = .allocate(capacity: Self.capacity)
        right.initialize(repeating: 0, count: Self.capacity)
        state = .allocate(capacity: 1)
        state.initialize(to: State())
        configure(sampleRate: 48_000)
    }

    deinit {
        left.deallocate()
        right.deallocate()
        state.deallocate()
    }

    /// IO が止まっている間に呼ぶ
    func configure(sampleRate fs: Double) {
        var length = 1
        while Double(length) < EchoSettings.timeRange.upperBound * fs + 4 { length <<= 1 }
        state.pointee.mask = min(length, Self.capacity) - 1
        // 遅延時間の追従: 時定数 0.08 秒 (目標の近くでゆっくり止まる)
        state.pointee.smoothing = Float(1 - exp(-1 / (0.08 * fs)))
        // 繰り返しの経路の 1 次ローパス: 4.5 kHz
        state.pointee.lowpass = Float(1 - exp(-2 * Double.pi * 4500 / fs))
    }

    // MARK: - オーディオスレッド

    /// オンにした直後に呼ぶ。前回の残りのエコーが鳴らないよう消し、遅延は目標から始める
    func reset(delaySamples: Float) {
        let used = state.pointee.mask + 1
        left.update(repeating: 0, count: used)
        right.update(repeating: 0, count: used)
        state.pointee.delay = clampDelay(delaySamples)
        state.pointee.lowLeft = 0
        state.pointee.lowRight = 0
    }

    func process(left inLeft: UnsafeMutablePointer<Float>, right inRight: UnsafeMutablePointer<Float>, frames: Int,
                 delaySamples: Float, feedback: Float, mix: Float, pingPong: Bool) {
        var s = state.pointee
        let target = clampDelay(delaySamples)

        for i in 0..<frames {
            let step = (target - s.delay) * s.smoothing
            s.delay += min(max(step, -Self.maxDelaySlew), Self.maxDelaySlew)

            // 小数の遅延は隣り合う 2 サンプルを直線補間して読む
            let position = Float(s.writeIndex) - s.delay
            let base = Int(position.rounded(.down))
            let frac = position - Float(base)
            let i0 = base & s.mask, i1 = (base + 1) & s.mask
            let echoLeft = left[i0] + (left[i1] - left[i0]) * frac
            let echoRight = right[i0] + (right[i1] - right[i0]) * frac

            s.lowLeft += (echoLeft - s.lowLeft) * s.lowpass
            s.lowRight += (echoRight - s.lowRight) * s.lowpass

            let dryLeft = inLeft[i], dryRight = inRight[i]
            if pingPong {
                // 原音 (左右を混ぜたもの) は左にだけ入れ、繰り返しは左右を入れ替えて戻す → 左、右、左…と交互に飛ぶ
                left[s.writeIndex] = (dryLeft + dryRight) * 0.5 + s.lowRight * feedback
                right[s.writeIndex] = s.lowLeft * feedback
            } else {
                left[s.writeIndex] = dryLeft + s.lowLeft * feedback
                right[s.writeIndex] = dryRight + s.lowRight * feedback
            }

            inLeft[i] = dryLeft + echoLeft * mix
            inRight[i] = dryRight + echoRight * mix
            s.writeIndex = (s.writeIndex + 1) & s.mask
        }
        state.pointee = s
    }

    private func clampDelay(_ samples: Float) -> Float {
        min(max(samples, 1), Float(state.pointee.mask - 2))
    }
}
