import AudioToolbox
import Foundation

/// 環境エフェクト (部屋の響き) のプリセット
enum RoomPreset: String, Codable, CaseIterable, Identifiable {
    case off, studio, liveHouse, hall, largeHall, arena, cathedral

    var id: String { rawValue }

    var name: String {
        switch self {
        case .off: return "オフ"
        case .studio: return "スタジオ"
        case .liveHouse: return "ライブハウス"
        case .hall: return "コンサートホール"
        case .largeHall: return "大ホール"
        case .arena: return "アリーナ"
        case .cathedral: return "大聖堂"
        }
    }

    /// AUMatrixReverb の工場プリセット番号。名前ではなく、実測した残響時間 (RT60) が実際の空間に近いものを選んだ
    var factoryPreset: Int32? {
        switch self {
        case .off: return nil
        case .studio: return 0       // Small Room: 0.29 秒
        case .liveHouse: return 9    // Large Room 2: 0.95 秒
        case .hall: return 11        // Medium Hall 3: 1.77 秒
        case .largeHall: return 4    // Large Hall: 2.63 秒
        case .arena: return 12       // Large Hall 2: 2.76 秒、最初の反射が 42 ms と遅い
        case .cathedral: return 8    // Cathedral: 7.64 秒
        }
    }

    /// 選んだときの残響の量 (0...1)。広い空間ほど多めにする
    var defaultMix: Double {
        switch self {
        case .off: return 0
        case .studio: return 0.15
        case .liveHouse: return 0.2
        case .hall: return 0.25
        case .largeHall: return 0.28
        case .arena: return 0.3
        case .cathedral: return 0.32 //default0.32
        }
    }
}

/// Apple の AUMatrixReverb で部屋の響きを付ける。BiquadEQ.process から EQ の後に呼ぶ
///
/// 自前で残響を作らず Apple の実装を使うのは、実績があり音質が確かなため。
/// 入力は render callback で渡し、出力は別のバッファに受けてから書き戻す。
final class RoomReverb {
    /// 1 回の process で扱える最大フレーム数 (呼ぶ側がこれ以下に分割する)
    static let maxFrames = 4096

    private let unit: AudioUnit
    private let source: UnsafeMutablePointer<RoomReverbSource>
    private let wetLeft: UnsafeMutablePointer<Float>
    private let wetRight: UnsafeMutablePointer<Float>
    private let outputList: UnsafeMutableAudioBufferListPointer
    private let timeStamp: UnsafeMutablePointer<AudioTimeStamp>

    /// AUMatrixReverb が見つからないときは nil
    init?() {
        var description = AudioComponentDescription(
            componentType: kAudioUnitType_Effect, componentSubType: kAudioUnitSubType_MatrixReverb,
            componentManufacturer: kAudioUnitManufacturer_Apple, componentFlags: 0, componentFlagsMask: 0)
        var instance: AudioUnit?
        guard let component = AudioComponentFindNext(nil, &description),
              AudioComponentInstanceNew(component, &instance) == noErr, let instance else { return nil }
        unit = instance

        source = .allocate(capacity: 1)
        wetLeft = .allocate(capacity: Self.maxFrames)
        wetRight = .allocate(capacity: Self.maxFrames)
        source.initialize(to: RoomReverbSource(left: wetLeft, right: wetRight))
        outputList = AudioBufferList.allocate(maximumBuffers: 2)
        timeStamp = .allocate(capacity: 1)
        var stamp = AudioTimeStamp()
        stamp.mFlags = .sampleTimeValid
        timeStamp.initialize(to: stamp)

        var maxFrames = UInt32(Self.maxFrames)
        AudioUnitSetProperty(unit, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0,
                             &maxFrames, UInt32(MemoryLayout<UInt32>.size))
        var callback = AURenderCallbackStruct(inputProc: renderInput, inputProcRefCon: UnsafeMutableRawPointer(source))
        AudioUnitSetProperty(unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0,
                             &callback, UInt32(MemoryLayout<AURenderCallbackStruct>.size))
        configure(sampleRate: 48_000)
    }

    deinit {
        AudioUnitUninitialize(unit)
        AudioComponentInstanceDispose(unit)
        source.deallocate()
        wetLeft.deallocate()
        wetRight.deallocate()
        free(outputList.unsafeMutablePointer)
        timeStamp.deallocate()
    }

    /// サンプルレートを合わせる。オーディオスレッドが process を呼んでいない間 (IO 停止中) に呼ぶこと
    func configure(sampleRate: Double) {
        AudioUnitUninitialize(unit)
        var format = AudioStreamBasicDescription(
            mSampleRate: sampleRate, mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kAudioFormatFlagsNativeFloatPacked | kAudioFormatFlagIsNonInterleaved,
            mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2,
            mBitsPerChannel: 32, mReserved: 0)
        let size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &format, size)
        AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 0, &format, size)
        AudioUnitInitialize(unit)
    }

    // AU のプリセットとパラメーターは再生中に変えてよい作りになっている (AVAudioUnitReverb と同じ使い方)

    /// 部屋の種類を変える (メインスレッド)。プリセットを読み込むと量も上書きされるので、続けて setMix を呼ぶ
    func load(_ preset: RoomPreset) {
        guard let number = preset.factoryPreset else { return }
        var auPreset = AUPreset(presetNumber: number, presetName: nil)
        AudioUnitSetProperty(unit, kAudioUnitProperty_PresentPreset, kAudioUnitScope_Global, 0,
                             &auPreset, UInt32(MemoryLayout<AUPreset>.size))
    }

    /// 量を変える (メインスレッド)。0 = 原音のみ … 1 = 響きのみ
    func setMix(_ mix: Double) {
        AudioUnitSetParameter(unit, AudioUnitParameterID(kReverbParam_DryWetMix), kAudioUnitScope_Global, 0,
                              AudioUnitParameterValue(mix.clamped(to: 0...1) * 100), 0)
    }

    // MARK: - オーディオスレッド

    /// 前回の残響を消す。オフからオンに切り替えたときに、昔の響きが鳴らないようにする
    func reset() {
        AudioUnitReset(unit, kAudioUnitScope_Global, 0)
    }

    /// left / right に響きを足して書き戻す。frames は maxFrames 以下
    func process(left: UnsafeMutablePointer<Float>, right: UnsafeMutablePointer<Float>, frames: Int) {
        source.pointee = RoomReverbSource(left: left, right: right)
        let byteSize = UInt32(frames * MemoryLayout<Float>.size)
        outputList[0] = AudioBuffer(mNumberChannels: 1, mDataByteSize: byteSize, mData: wetLeft)
        outputList[1] = AudioBuffer(mNumberChannels: 1, mDataByteSize: byteSize, mData: wetRight)

        var flags = AudioUnitRenderActionFlags()
        let status = AudioUnitRender(unit, &flags, timeStamp, 0, UInt32(frames), outputList.unsafeMutablePointer)
        timeStamp.pointee.mSampleTime += Double(frames)
        // 失敗したら原音のまま (音を止めない)
        guard status == noErr else { return }
        left.update(from: wetLeft, count: frames)
        right.update(from: wetRight, count: frames)
    }
}

/// render callback に渡す入力の場所
private struct RoomReverbSource {
    var left: UnsafeMutablePointer<Float>
    var right: UnsafeMutablePointer<Float>
}

/// AU が入力を取りに来たときに、EQ 後の音を渡す (オーディオスレッド。配列などを作らない)
private let renderInput: AURenderCallback = { refCon, _, _, _, frames, ioData in
    guard let ioData else { return noErr }
    let source = refCon.assumingMemoryBound(to: RoomReverbSource.self).pointee
    let buffers = UnsafeMutableAudioBufferListPointer(ioData)
    guard buffers.count >= 2 else { return noErr }
    supply(source.left, to: &buffers[0], frames: frames)
    supply(source.right, to: &buffers[1], frames: frames)
    return noErr
}

private func supply(_ channel: UnsafeMutablePointer<Float>, to buffer: inout AudioBuffer, frames: UInt32) {
    if let data = buffer.mData {
        data.copyMemory(from: channel, byteCount: Int(frames) * MemoryLayout<Float>.size)
    } else {
        buffer.mData = UnsafeMutableRawPointer(channel)
    }
}
