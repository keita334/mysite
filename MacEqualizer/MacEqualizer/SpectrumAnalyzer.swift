import Accelerate
import Combine
import Foundation

/// EQ 後の出力を FFT して、表示用の周波数ごとのレベル (dBFS) を作る
///
/// 各表示点のレベルは、その点を中心とする 1/12 オクターブの帯域に入る FFT ビンのパワーの合計。
/// 帯域ごとに合計するので、ピンクノイズはほぼ平らに、フルスケールの正弦波は 0 dB に表示される。
/// 帯域は隣の点と重なる (点の間隔は 1/24 オクターブ) ので、ノイズのばらつきで線がギザギザになりにくい。
final class SpectrumAnalyzer: ObservableObject {
    static let fftSize = 8192
    static let pointCount = 240
    static let floorDB: Float = -120
    /// 表示点の周波数 (20 Hz〜20 kHz を対数で等分)
    static let frequencies: [Double] = (0..<pointCount).map {
        20 * pow(1000, Double($0) / Double(pointCount - 1))
    }

    /// 中心周波数に対する帯域の下端・上端の比 (± 1/24 オクターブ = 幅 1/12 オクターブ)
    private static let halfBandRatio = (lower: pow(2, -1.0 / 24), upper: pow(2, 1.0 / 24))

    /// 帯域ごとの直近の音量 (dBFS, RMS)。ビジュアル表示で拍を見つけるのに使う
    struct Energy {
        var bass: Float
        var mid: Float
        var high: Float
        var overall: Float

        static let silent = Energy(bass: floorDB, mid: floorDB, high: floorDB, overall: floorDB)
    }

    @Published private(set) var levels = [Float](repeating: floorDB, count: pointCount)
    /// 毎フレーム読む側 (ビジュアル表示) が自分で取りに来るので、変更を通知しない
    private(set) var energy = Energy.silent

    /// グラフの後ろにアナライザーを表示するか
    @Published var isEnabled = true {
        didSet { if isEnabled != oldValue { updateTimer() } }
    }

    /// ビジュアル表示中は、グラフのアナライザーを切っていても分析を続ける
    var isVisualizerActive = false {
        didSet { if isVisualizerActive != oldValue { updateTimer() } }
    }

    private let processor: BiquadEQ
    private let fft: vDSP.FFT<DSPSplitComplex>
    private let window: [Float]
    private let powerScale: Float
    private var samples = [Float](repeating: 0, count: fftSize)
    private var real = [Float](repeating: 0, count: fftSize / 2)
    private var imag = [Float](repeating: 0, count: fftSize / 2)
    private var power = [Float](repeating: 0, count: fftSize / 2)
    /// 表示点ごとの帯域パワーの移動平均
    private var averagedPower = [Float](repeating: 0, count: pointCount)
    private var timer: Timer?
    private var isStarted = false

    init(processor: BiquadEQ) {
        self.processor = processor
        let n = Self.fftSize
        fft = vDSP.FFT(log2n: vDSP_Length(log2(Double(n))), radix: .radix2, ofType: DSPSplitComplex.self)!
        window = vDSP.window(ofType: Float.self, usingSequence: .hanningDenormalized, count: n, isHalfWindow: false)
        // vDSP の実数 FFT は 2 倍でスケールされるので、片側スペクトルのパワー合計は n × Σw² × 振幅² になる
        powerScale = 1 / (Float(n) * vDSP.sum(vDSP.square(window)))
    }

    deinit {
        timer?.invalidate()
    }

    /// 音声の取り込みが始まったら呼ぶ
    func start() {
        isStarted = true
        updateTimer()
    }

    func stop() {
        isStarted = false
        updateTimer()
    }

    private func updateTimer() {
        let shouldRun = isStarted && (isEnabled || isVisualizerActive)
        if shouldRun, timer == nil {
            let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.refresh() }
            // スライダーをドラッグしている間も止まらないように .common モードで回す
            RunLoop.main.add(timer, forMode: .common)
            self.timer = timer
        } else if !shouldRun, let timer {
            timer.invalidate()
            self.timer = nil
            levels = [Float](repeating: Self.floorDB, count: Self.pointCount)
            averagedPower = [Float](repeating: 0, count: Self.pointCount)
            energy = .silent
        }
    }

    /// 最新の出力を 1 回分析して levels を更新する
    func refresh() {
        let n = Self.fftSize
        samples.withUnsafeMutableBufferPointer { processor.copyLatestOutput(into: $0.baseAddress!, count: n) }
        energy = measureEnergy(sampleRate: processor.sampleRate)
        vDSP.multiply(samples, window, result: &samples)

        samples.withUnsafeBufferPointer { samplePointer in
            real.withUnsafeMutableBufferPointer { realPointer in
                imag.withUnsafeMutableBufferPointer { imagPointer in
                    var split = DSPSplitComplex(realp: realPointer.baseAddress!, imagp: imagPointer.baseAddress!)
                    samplePointer.baseAddress!.withMemoryRebound(to: DSPComplex.self, capacity: n / 2) {
                        vDSP_ctoz($0, 2, &split, 1, vDSP_Length(n / 2))
                    }
                    fft.forward(input: split, output: &split)
                    power.withUnsafeMutableBufferPointer {
                        vDSP_zvmags(&split, 1, $0.baseAddress!, 1, vDSP_Length(n / 2))
                    }
                }
            }
        }
        // 0 番には直流とナイキストが詰め込まれているので使わない
        power[0] = 0

        let binWidth = processor.sampleRate / Double(n)
        let frequencies = Self.frequencies
        var next = levels
        for i in 0..<Self.pointCount {
            let lowBin = frequencies[i] * Self.halfBandRatio.lower / binWidth
            let highBin = frequencies[i] * Self.halfBandRatio.upper / binWidth

            var bandPower: Float = 0
            let first = Int(lowBin.rounded(.up)), last = min(Int(highBin), n / 2 - 1)
            if first <= last {
                for k in first...last { bandPower += power[k] }
            } else {
                // 帯域が 1 ビンより狭い低域は、隣り合うビンを補間して帯域幅の分だけ取る
                let center = frequencies[i] / binWidth
                let k = min(Int(center), n / 2 - 2), frac = Float(center - Double(k))
                bandPower = (power[k] * (1 - frac) + power[k + 1] * frac) * Float(highBin - lowBin)
            }

            averagedPower[i] += (bandPower - averagedPower[i]) * 0.5
            let db = max(10 * log10(averagedPower[i] * powerScale + 1e-20), Self.floorDB)
            // 上がるときは素早く、下がるときはゆっくり (1 フレームで最大 1.2 dB)
            next[i] = db > next[i] ? next[i] + (db - next[i]) * 0.6 : max(db, next[i] - 1.2)
        }
        levels = next
    }

    /// 直近 1024 サンプル (48 kHz で約 21 ms) の帯域ごとの RMS。FFT の窓 (約 170 ms) より短いので拍の立ち上がりに素早く反応する。
    /// 窓をかける前の samples に対して呼ぶ
    private func measureEnergy(sampleRate fs: Double) -> Energy {
        let count = 2048, measured = 1024
        let start = Self.fftSize - count

        // フィルタは毎回まっさらな状態から通し、立ち上がりが落ち着いた後半だけを測る
        func rmsDB(_ sections: [Biquad]) -> Float {
            var z = [Double](repeating: 0, count: sections.count * 2)
            var sum = 0.0
            for i in 0..<count {
                var y = Double(samples[start + i])
                for (k, s) in sections.enumerated() {
                    let x = y
                    y = s.b0 * x + z[2 * k]
                    z[2 * k] = s.b1 * x - s.a1 * y + z[2 * k + 1]
                    z[2 * k + 1] = s.b2 * x - s.a2 * y
                }
                if i >= count - measured { sum += y * y }
            }
            return max(Float(10 * log10(sum / Double(measured) + 1e-20)), Self.floorDB)
        }

        let lowPass150 = Biquad.butterworth(highPass: false, frequency: 150, order: 4, sampleRate: fs)
        let highPass150 = Biquad.butterworth(highPass: true, frequency: 150, order: 2, sampleRate: fs)
        let lowPass4k = Biquad.butterworth(highPass: false, frequency: 4000, order: 2, sampleRate: fs)
        let highPass4k = Biquad.butterworth(highPass: true, frequency: 4000, order: 2, sampleRate: fs)
        return Energy(bass: rmsDB(lowPass150), mid: rmsDB(highPass150 + lowPass4k),
                      high: rmsDB(highPass4k), overall: rmsDB([]))
    }
}
