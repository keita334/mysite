import AVFoundation
import Combine

/// AVAudioEngine を使ったオーディオ再生 + 10バンドイコライザー
///
/// 信号の流れ: AVAudioPlayerNode → AVAudioUnitEQ → mainMixerNode → 出力デバイス
final class AudioEngine: ObservableObject {

    // MARK: - 定数

    /// 各バンドの中心周波数 (Hz)
    static let frequencies: [Float] = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    /// スライダーで調整できるゲインの範囲 (dB)
    static let gainRange: ClosedRange<Float> = -12...12
    /// 各バンドの帯域幅 (オクターブ)
    static let bandwidth: Float = 1.0

    // MARK: - EQ の状態

    @Published var gains: [Float] = Array(repeating: 0, count: AudioEngine.frequencies.count) {
        didSet {
            applyGains()
            UserDefaults.standard.set(gains, forKey: Keys.gains)
        }
    }

    /// プリアンプ (EQ 全体のゲイン)。ブーストで音割れする場合は下げる
    @Published var preamp: Float = 0 {
        didSet {
            eq.globalGain = preamp
            UserDefaults.standard.set(preamp, forKey: Keys.preamp)
        }
    }

    @Published var isBypassed = false {
        didSet { eq.bypass = isBypassed }
    }

    @Published var volume: Float = 0.8 {
        didSet { engine.mainMixerNode.outputVolume = volume }
    }

    // MARK: - 再生の状態

    @Published private(set) var isPlaying = false
    @Published private(set) var fileName: String?
    @Published private(set) var currentTime: TimeInterval = 0
    @Published private(set) var duration: TimeInterval = 0
    @Published var errorMessage: String?

    var hasFile: Bool { file != nil }

    // MARK: - 内部

    private enum Keys {
        static let gains = "eq.gains"
        static let preamp = "eq.preamp"
    }

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let eq = AVAudioUnitEQ(numberOfBands: AudioEngine.frequencies.count)

    private var file: AVAudioFile?
    /// 現在スケジュールしているセグメントの開始フレーム
    private var segmentStartFrame: AVAudioFramePosition = 0
    private var needsScheduling = true
    /// 停止・シーク後に古いセグメントの完了通知を無視するための世代番号
    private var scheduleGeneration = 0
    private var timer: Timer?
    private var configObserver: NSObjectProtocol?

    // MARK: - 初期化

    init() {
        for (band, frequency) in zip(eq.bands, Self.frequencies) {
            band.filterType = .parametric
            band.frequency = frequency
            band.bandwidth = Self.bandwidth
            band.gain = 0
            band.bypass = false
        }

        engine.attach(player)
        engine.attach(eq)
        engine.connect(player, to: eq, format: nil)
        engine.connect(eq, to: engine.mainMixerNode, format: nil)
        engine.mainMixerNode.outputVolume = volume

        // 前回の設定を復元
        if let saved = UserDefaults.standard.array(forKey: Keys.gains) as? [Float],
           saved.count == Self.frequencies.count {
            gains = saved
        }
        preamp = UserDefaults.standard.float(forKey: Keys.preamp)
        // init 内ではプロパティオブザーバが呼ばれないので明示的に反映する
        applyGains()
        eq.globalGain = preamp

        // 出力デバイスの変更などでエンジンが止まったら再開する
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: engine,
            queue: .main
        ) { [weak self] _ in
            self?.handleConfigurationChange()
        }
    }

    deinit {
        if let configObserver { NotificationCenter.default.removeObserver(configObserver) }
        timer?.invalidate()
    }

    // MARK: - EQ 操作

    func setGain(_ gain: Float, forBand index: Int) {
        guard gains.indices.contains(index) else { return }
        gains[index] = min(max(gain, Self.gainRange.lowerBound), Self.gainRange.upperBound)
    }

    func apply(_ preset: EQPreset) {
        gains = preset.gains
    }

    func resetEQ() {
        gains = Array(repeating: 0, count: Self.frequencies.count)
        preamp = 0
    }

    private func applyGains() {
        for (band, gain) in zip(eq.bands, gains) {
            band.gain = gain
        }
    }

    // MARK: - ファイル読み込み

    func load(url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }

        do {
            let newFile = try AVAudioFile(forReading: url)
            stop()
            engine.stop()

            // ファイルのフォーマットでノードを繋ぎ直す
            let format = newFile.processingFormat
            engine.disconnectNodeOutput(player)
            engine.disconnectNodeOutput(eq)
            engine.connect(player, to: eq, format: format)
            engine.connect(eq, to: engine.mainMixerNode, format: format)
            engine.prepare()

            file = newFile
            fileName = url.lastPathComponent
            duration = Double(newFile.length) / format.sampleRate
            currentTime = 0
            segmentStartFrame = 0
            needsScheduling = true
            errorMessage = nil
        } catch {
            errorMessage = "ファイルを開けませんでした: \(error.localizedDescription)"
        }
    }

    // MARK: - 再生操作

    func togglePlayPause() {
        isPlaying ? pause() : play()
    }

    func play() {
        guard file != nil else { return }
        do {
            if !engine.isRunning {
                try engine.start()
            }
        } catch {
            errorMessage = "オーディオエンジンを開始できませんでした: \(error.localizedDescription)"
            return
        }
        if needsScheduling {
            scheduleSegment()
        }
        player.play()
        isPlaying = true
        startTimer()
    }

    func pause() {
        player.pause()
        isPlaying = false
        stopTimer()
        updateCurrentTime()
    }

    func stop() {
        scheduleGeneration += 1
        player.stop()
        isPlaying = false
        stopTimer()
        segmentStartFrame = 0
        currentTime = 0
        needsScheduling = true
    }

    func seek(to time: TimeInterval) {
        guard let file else { return }
        let sampleRate = file.processingFormat.sampleRate
        let wasPlaying = isPlaying

        scheduleGeneration += 1
        player.stop()

        let frame = AVAudioFramePosition(time * sampleRate)
        segmentStartFrame = min(max(frame, 0), file.length)
        currentTime = Double(segmentStartFrame) / sampleRate
        needsScheduling = true

        if wasPlaying {
            play()
        }
    }

    // MARK: - 内部処理

    private func scheduleSegment() {
        guard let file else { return }
        let remaining = file.length - segmentStartFrame
        guard remaining > 0 else {
            stop()
            return
        }

        scheduleGeneration += 1
        let generation = scheduleGeneration
        player.scheduleSegment(
            file,
            startingFrame: segmentStartFrame,
            frameCount: AVAudioFrameCount(remaining),
            at: nil,
            completionCallbackType: .dataPlayedBack
        ) { [weak self] _ in
            DispatchQueue.main.async {
                self?.segmentDidFinish(generation: generation)
            }
        }
        needsScheduling = false
    }

    private func segmentDidFinish(generation: Int) {
        // 停止やシークで差し替えられたセグメントの通知は無視
        guard generation == scheduleGeneration else { return }
        stop()
    }

    private func updateCurrentTime() {
        guard file != nil,
              let nodeTime = player.lastRenderTime,
              let playerTime = player.playerTime(forNodeTime: nodeTime) else { return }
        let frame = segmentStartFrame + playerTime.sampleTime
        currentTime = min(max(Double(frame) / playerTime.sampleRate, 0), duration)
    }

    private func startTimer() {
        stopTimer()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.updateCurrentTime()
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func handleConfigurationChange() {
        guard isPlaying else { return }
        do {
            try engine.start()
            player.play()
        } catch {
            errorMessage = "出力デバイスの変更後に再開できませんでした: \(error.localizedDescription)"
            pause()
        }
    }

    // MARK: - 周波数特性 (表示用)

    /// 指定周波数での EQ 全体の応答 (dB)。RBJ Audio EQ Cookbook のピーキングフィルタで近似
    func response(atFrequency f: Double, sampleRate: Double = 48_000) -> Double {
        guard !isBypassed else { return 0 }
        var total = Double(preamp)
        for (f0, gain) in zip(Self.frequencies, gains) where gain != 0 {
            total += Self.peakingResponse(
                frequency: f, centerFrequency: Double(f0), gainDB: Double(gain),
                bandwidth: Double(Self.bandwidth), sampleRate: sampleRate
            )
        }
        return total
    }

    static func peakingResponse(frequency f: Double, centerFrequency f0: Double,
                                gainDB: Double, bandwidth: Double, sampleRate fs: Double) -> Double {
        let a = pow(10, gainDB / 40)
        let w0 = 2 * Double.pi * f0 / fs
        let alpha = sin(w0) * sinh(log(2) / 2 * bandwidth * w0 / sin(w0))

        let b0 = 1 + alpha * a, b1 = -2 * cos(w0), b2 = 1 - alpha * a
        let a0 = 1 + alpha / a, a1 = -2 * cos(w0), a2 = 1 - alpha / a

        let w = 2 * Double.pi * f / fs
        let numRe = b0 + b1 * cos(w) + b2 * cos(2 * w)
        let numIm = -(b1 * sin(w) + b2 * sin(2 * w))
        let denRe = a0 + a1 * cos(w) + a2 * cos(2 * w)
        let denIm = -(a1 * sin(w) + a2 * sin(2 * w))

        let magnitudeSquared = (numRe * numRe + numIm * numIm) / (denRe * denRe + denIm * denIm)
        return 10 * log10(magnitudeSquared)
    }
}
