import AppKit
import Combine
import Foundation

/// EQ の設定と、Mac 全体の音に EQ をかける処理をまとめるモデル
final class EqualizerModel: ObservableObject {
    /// 画面表示用のサンプルレート。実際の処理は出力デバイスのレートで係数を計算する
    static let displaySampleRate = 48_000.0
    static let outputGainRange = -24.0...24.0

    @Published var bands: [EQBand] {
        didSet {
            pushToProcessor()
            scheduleSave()
        }
    }

    /// EQ 後にかける全体のゲイン。ブーストで音割れする場合は下げる
    @Published var outputGain: Double {
        didSet {
            pushToProcessor()
            scheduleSave()
        }
    }

    @Published var isBypassed = false {
        didSet { pushToProcessor() }
    }

    /// 環境エフェクト (ホールやスタジオの響き)。EQ の後にかかる
    @Published private(set) var room: RoomPreset {
        didSet {
            pushRoom()
            scheduleSave()
        }
    }

    /// エコー (実験的)。EQ の後、環境エフェクトの前にかかる
    @Published var echo: EchoSettings {
        didSet {
            systemTap.processor.updateEcho(echo)
            scheduleSave()
        }
    }

    /// 環境エフェクトの量 (0 = 原音のみ … 1 = 響きのみ)
    @Published var roomMix: Double {
        didSet {
            pushRoom()
            scheduleSave()
        }
    }

    @Published private(set) var isRunning = false
    @Published private(set) var outputDeviceName: String?
    @Published var errorMessage: String?

    let analyzer: SpectrumAnalyzer

    private enum Keys {
        static let settings = "eq.settings.v2"
    }

    private struct Settings: Codable {
        var bands: [EQBand]
        var outputGain: Double
        // 後から増えた項目。古い保存データにはないので省略可にする (ないと全体の読み込みに失敗する)
        var room: RoomPreset?
        var roomMix: Double?
        var echo: EchoSettings?
    }

    private let systemTap: SystemAudioTap
    private var saveWork: DispatchWorkItem?

    /// startsAudio: false にすると音声を扱わない (プレビューやテスト用)
    init(startsAudio: Bool = true) {
        let tap = SystemAudioTap()
        systemTap = tap
        analyzer = SpectrumAnalyzer(processor: tap.processor)

        if let data = UserDefaults.standard.data(forKey: Keys.settings),
           let saved = try? JSONDecoder().decode(Settings.self, from: data),
           saved.bands.map(\.type) == EQBand.defaults.map(\.type) {
            bands = saved.bands
            outputGain = saved.outputGain
            room = saved.room ?? .off
            roomMix = saved.roomMix ?? 0
            echo = saved.echo ?? EchoSettings()
        } else {
            bands = EQBand.defaults
            outputGain = 0
            room = .off
            roomMix = 0
            echo = EchoSettings()
        }
        pushToProcessor()
        pushRoom()
        systemTap.processor.updateEcho(echo)

        tap.onStart = { [weak self] name in
            self?.outputDeviceName = name
        }
        tap.onError = { [weak self] error in
            self?.errorMessage = "出力デバイスの切り替え後に再開できませんでした: \(error.localizedDescription)"
            self?.isRunning = false
            self?.analyzer.stop()
        }

        if startsAudio {
            start()
        }
    }

    // MARK: - 操作

    /// Mac 全体の音への EQ を開始する。失敗したときは「再開」ボタンからもう一度呼ぶ
    func start() {
        // 2 つ同時に動くと、互いの出力を取り込み合って発振する (最後は NaN になって無音になる)
        guard !Self.isAnotherInstanceRunning else {
            isRunning = false
            errorMessage = "MacEqualizer がすでに起動しています。2 つ同時に動かすと音が発振するため、こちらでは EQ を開始しません。もう一方を終了してから「再開」を押してください。"
            return
        }
        do {
            try systemTap.start()
            isRunning = true
            errorMessage = nil
            analyzer.start()
        } catch {
            isRunning = false
            errorMessage = "システムオーディオを取り込めませんでした: \(error.localizedDescription)"
        }
    }

    func apply(_ preset: EQPreset) {
        bands = preset.bands
    }

    /// 環境を選ぶ。量はその空間に合った値にする (広い空間ほど多め)
    func selectRoom(_ preset: RoomPreset) {
        roomMix = preset.defaultMix
        room = preset
    }

    func reset() {
        bands = EQBand.defaults
        outputGain = 0
    }

    func resetBand(_ index: Int) {
        bands[index] = EQBand.defaults[index]
    }

    var currentPresetName: String {
        EQPreset.all.first { $0.bands == bands }?.name ?? "カスタム"
    }

    // MARK: - 内部

    private static var isAnotherInstanceRunning: Bool {
        guard let bundleID = Bundle.main.bundleIdentifier else { return false }
        let me = ProcessInfo.processInfo.processIdentifier
        return NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .contains { $0.processIdentifier != me && !$0.isTerminated }
    }

    private func pushToProcessor() {
        systemTap.processor.update(bands: bands, outputGain: outputGain, bypass: isBypassed)
    }

    private func pushRoom() {
        systemTap.processor.updateRoom(room, mix: roomMix)
    }

    /// ドラッグ中は毎フレーム値が変わるので、止まってから保存する
    private func scheduleSave() {
        saveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let settings = Settings(bands: self.bands, outputGain: self.outputGain, room: self.room, roomMix: self.roomMix, echo: self.echo)
            if let data = try? JSONEncoder().encode(settings) {
                UserDefaults.standard.set(data, forKey: Keys.settings)
            }
        }
        saveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: work)
    }
}
