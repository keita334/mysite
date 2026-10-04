import CoreAudio
import Foundation
import os

/// Core Audio の Process Tap (macOS 14.2+) で Mac 全体の音を取り込み、EQ をかけて出力デバイスへ流す
///
/// 信号の流れ:
///   各アプリ (Apple Music など) → Process Tap → 集約デバイスの入力 → BiquadEQ → 集約デバイスの出力 (= 現在の出力デバイス)
///
/// タップは mutedWhenTapped なので、元の音はミュートされ EQ 後の音だけが聞こえる。
/// タップと集約デバイスは private なので、アプリが落ちても OS が片付けて元の音に戻る。
final class SystemAudioTap {
    let processor = BiquadEQ()

    /// 開始・作り直しのたびに、出力先デバイスの名前を渡して呼ばれる
    var onStart: ((String) -> Void)?
    /// 出力デバイスの切り替え後に再開できなかったときに呼ばれる
    var onError: ((Error) -> Void)?

    private(set) var isRunning = false

    private let logger = Logger(subsystem: Bundle.main.bundleIdentifier ?? "MacEqualizer", category: "SystemAudioTap")

    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?
    private var defaultDeviceListener: AudioObjectPropertyListenerBlock?
    private var sampleRateListener: AudioObjectPropertyListenerBlock?
    private var hasReceivedAudio = false
    private var audioCheckTimer: Timer?
    private var pendingRestart: DispatchWorkItem?

    deinit {
        stop()
    }

    // MARK: - 開始 / 停止

    func start() throws {
        guard !isRunning else { return }
        do {
            try setUp()
            isRunning = true
        } catch {
            tearDown()
            throw error
        }
    }

    func stop() {
        guard isRunning else { return }
        tearDown()
        isRunning = false
    }

    private func setUp() throws {
        // 出力デバイスの切り替えやサンプルレートの変更があったら作り直す。
        // デバイスを読む前に登録しておかないと、その間の切り替えを取りこぼす
        let restart: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            self?.scheduleRestart()
        }
        defaultDeviceListener = restart
        var defaultDeviceAddress = address(kAudioHardwarePropertyDefaultOutputDevice)
        AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &defaultDeviceAddress, .main, restart)

        // 1. 自分自身を除いた全プロセスの音をステレオで取り込むタップ。
        //    自分を除外しないと、EQ 後に出力した音を再び取り込んでループする
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [try ownProcessObjectID()])
        description.name = "MacEqualizer"
        description.muteBehavior = .mutedWhenTapped
        description.isPrivate = true
        try check(AudioHardwareCreateProcessTap(description, &tapID), "Process Tap の作成")

        // 2. 現在の出力デバイスとタップをまとめた集約デバイス。
        //    IOProc の入力にタップの音が届き、出力が実際のスピーカーへ出る
        let outputDevice: AudioObjectID = try getProperty(
            AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice)
        let outputUID = try getString(outputDevice, kAudioDevicePropertyDeviceUID)
        let aggregateDescription: [String: Any] = [
            kAudioAggregateDeviceNameKey: "MacEqualizer",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapUIDKey: description.uuid.uuidString,
                kAudioSubTapDriftCompensationKey: true,
            ]],
        ]
        try check(AudioHardwareCreateAggregateDevice(aggregateDescription as CFDictionary, &aggregateID),
                  "集約デバイスの作成")

        let sampleRate: Float64 = try getProperty(aggregateID, kAudioDevicePropertyNominalSampleRate)
        processor.sampleRate = sampleRate
        processor.resetState()
        hasReceivedAudio = false

        // 3. 集約デバイスの入力は「出力デバイス自身の入力ストリーム → タップ」の順に並ぶ。
        //    出力デバイスにマイクがある場合 (AirPods など) はそれを飛ばしてタップだけ読む
        let deviceInputStreams = streamCount(outputDevice, scope: kAudioObjectPropertyScopeInput)

        try check(AudioDeviceCreateIOProcIDWithBlock(&ioProcID, aggregateID, nil) {
            [processor] _, inputData, _, outputData, _ in
            processor.process(input: inputData, skipInputBuffers: deviceInputStreams, output: outputData)
        }, "IOProc の作成")

        if deviceInputStreams > 0 {
            // マイクを使うと Bluetooth ヘッドホンが通話用の低音質モードに切り替わるので、使わないと伝える
            disableInputStreams(count: deviceInputStreams)
        }

        try check(AudioDeviceStart(aggregateID, ioProcID), "集約デバイスの開始")

        onStart?((try? getString(outputDevice, kAudioObjectPropertyName)) ?? outputUID)
        audioCheckTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.checkForAudio()
        }

        logger.notice("""
            started: output=\(outputUID, privacy: .public) rate=\(sampleRate) \
            deviceInputStreams=\(deviceInputStreams) aggregateInputStreams=\(self.streamCount(self.aggregateID, scope: kAudioObjectPropertyScopeInput))
            """)

        sampleRateListener = restart
        var sampleRateAddress = address(kAudioDevicePropertyNominalSampleRate)
        AudioObjectAddPropertyListenerBlock(aggregateID, &sampleRateAddress, .main, restart)
    }

    private func tearDown() {
        audioCheckTimer?.invalidate()
        audioCheckTimer = nil
        pendingRestart?.cancel()
        pendingRestart = nil
        if let listener = defaultDeviceListener {
            var addr = address(kAudioHardwarePropertyDefaultOutputDevice)
            AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, .main, listener)
            defaultDeviceListener = nil
        }
        if let listener = sampleRateListener, aggregateID != kAudioObjectUnknown {
            var addr = address(kAudioDevicePropertyNominalSampleRate)
            AudioObjectRemovePropertyListenerBlock(aggregateID, &addr, .main, listener)
        }
        sampleRateListener = nil

        if let ioProcID, aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, ioProcID)
            AudioDeviceDestroyIOProcID(aggregateID, ioProcID)
        }
        ioProcID = nil
        if aggregateID != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }

    /// 1 回の切り替えで通知が続けて届くことがあるので、まとめて 1 回だけ作り直す。
    /// リスナーの中から自分自身を外すとデッドロックし得るので、必ずキュー経由で呼ぶ
    private func scheduleRestart() {
        pendingRestart?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.restart() }
        pendingRestart = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2, execute: work)
    }

    private func restart() {
        guard isRunning else { return }
        logger.notice("restarting after device change")
        tearDown()
        do {
            try setUp()
        } catch {
            tearDown()
            isRunning = false
            onError?(error)
        }
    }

    /// 初めて音が届いたらログに残す (権限がないと無音しか届かないので、その切り分け用)
    private func checkForAudio() {
        let peak = processor.consumePeak()
        if peak > 0, !hasReceivedAudio {
            hasReceivedAudio = true
            logger.notice("receiving audio (peak \(peak))")
        }
    }

    // MARK: - Core Audio ヘルパー

    private func ownProcessObjectID() throws -> AudioObjectID {
        var addr = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
        var pid = getpid()
        var objectID = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        try check(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr,
                                             UInt32(MemoryLayout<pid_t>.size), &pid, &size, &objectID),
                  "自プロセスの取得")
        guard objectID != kAudioObjectUnknown else {
            throw CoreAudioError(operation: "自プロセスの取得", status: kAudioHardwareBadObjectError)
        }
        return objectID
    }

    private func streamCount(_ device: AudioObjectID, scope: AudioObjectPropertyScope) -> Int {
        var addr = address(kAudioDevicePropertyStreams, scope: scope)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &addr, 0, nil, &size) == noErr else { return 0 }
        return Int(size) / MemoryLayout<AudioStreamID>.size
    }

    /// IOProc に集約デバイスの先頭 count 個の入力ストリームを使わないと伝える (失敗しても続行する)
    private func disableInputStreams(count: Int) {
        guard let ioProcID else { return }
        var addr = address(kAudioDevicePropertyIOProcStreamUsage, scope: kAudioObjectPropertyScopeInput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(aggregateID, &addr, 0, nil, &size) == noErr else { return }

        // AudioHardwareIOProcStreamUsage は末尾が可変長配列なので生メモリで扱う
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<UnsafeRawPointer>.alignment)
        defer { raw.deallocate() }
        raw.storeBytes(of: unsafeBitCast(ioProcID, to: UnsafeMutableRawPointer.self), as: UnsafeMutableRawPointer.self)
        guard AudioObjectGetPropertyData(aggregateID, &addr, 0, nil, &size, raw) == noErr else { return }

        let numberOffset = MemoryLayout<AudioHardwareIOProcStreamUsage>.offset(of: \.mNumberStreams)!
        let flagsOffset = MemoryLayout<AudioHardwareIOProcStreamUsage>.offset(of: \.mStreamIsOn)!
        let streams = Int(raw.load(fromByteOffset: numberOffset, as: UInt32.self))
        for i in 0..<min(count, streams) {
            raw.storeBytes(of: UInt32(0), toByteOffset: flagsOffset + i * MemoryLayout<UInt32>.size, as: UInt32.self)
        }
        let status = AudioObjectSetPropertyData(aggregateID, &addr, 0, nil, size, raw)
        if status != noErr {
            logger.error("failed to disable device input streams: \(status)")
        }
    }

    private func address(_ selector: AudioObjectPropertySelector,
                         scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    private func getProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> T {
        var addr = address(selector)
        var size = UInt32(MemoryLayout<T>.size)
        let value = UnsafeMutablePointer<T>.allocate(capacity: 1)
        defer { value.deallocate() }
        try check(AudioObjectGetPropertyData(object, &addr, 0, nil, &size, value), "プロパティ取得")
        return value.pointee
    }

    private func getString(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> String {
        var addr = address(selector)
        var size = UInt32(MemoryLayout<CFString>.size)
        var value: Unmanaged<CFString>?
        try check(AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value), "プロパティ取得")
        guard let value else { throw CoreAudioError(operation: "プロパティ取得", status: kAudioHardwareUnspecifiedError) }
        return value.takeRetainedValue() as String
    }

    private func check(_ status: OSStatus, _ operation: String) throws {
        guard status == noErr else { throw CoreAudioError(operation: operation, status: status) }
    }
}

struct CoreAudioError: LocalizedError {
    let operation: String
    let status: OSStatus

    var errorDescription: String? {
        "\(operation)に失敗しました (OSStatus \(status))"
    }
}
