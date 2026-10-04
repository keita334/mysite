import CoreAudio
import Foundation
import os

/// オーディオスレッドで動く処理の本体。EQ (全バンドの双二次フィルタを直列) → エコー → 環境エフェクト (残響) の順にかける
///
/// 係数はメインスレッドで計算して pending に書き、オーディオスレッドが trylock で取り込む。
/// オーディオスレッドではメモリ確保やブロックするロックを使わないよう、状態はすべて生ポインタに置く。
/// 最終的な出力はリングバッファにも書き、アナライザーが読む。
final class BiquadEQ {
    static let maxChannels = 2
    /// 8 バンドすべてを最大スロープにしても収まる段数
    static let maxSections = 24
    static let ringSize = 1 << 15

    /// [段数, 出力ゲイン (リニア), バイパス (0/1), 環境エフェクト (0/1),
    ///  エコー (0/1), エコーの遅延 (サンプル), 繰り返し, 量, ピンポン (0/1)] + 段ごとの [b0, b1, b2, a1, a2]
    private static let headerCount = 9
    private static let paramCount = headerCount + maxSections * 5
    private static let stateCount = maxChannels * maxSections * 2

    private let active: UnsafeMutablePointer<Float>
    private let pending: UnsafeMutablePointer<Float>
    private let hasPending: UnsafeMutablePointer<Bool>
    /// チャンネル × 段ごとの遅延素子 (z1, z2)
    private let state: UnsafeMutablePointer<Float>
    private let lock: UnsafeMutablePointer<os_unfair_lock>
    /// 入力のピーク値 (音が届いているかの確認用)
    private let peak: UnsafeMutablePointer<Float>
    /// EQ 後の出力 (L+R の平均) のリングバッファと書き込み位置
    private let ring: UnsafeMutablePointer<Float>
    private let ringWriteIndex: UnsafeMutablePointer<Int>
    /// 環境エフェクト。AUMatrixReverb が使えない環境では nil (EQ だけで動く)
    private let room = RoomReverb()
    /// EQ 後の左右を残響に渡すための作業領域 (左 maxFrames + 右 maxFrames)
    private let work: UnsafeMutablePointer<Float>
    private let roomWasOn: UnsafeMutablePointer<Bool>
    private let echo = EchoEffect()
    private let echoWasOn: UnsafeMutablePointer<Bool>

    // メインスレッドだけが触るパラメータ
    private var bands = EQBand.defaults
    private var outputGain = 0.0
    private var bypass = false
    private var roomPreset = RoomPreset.off
    private var echoSettings = EchoSettings()
    /// AU に読み込み済みのプリセット (同じものを読み直さない)
    private var loadedRoomPreset: RoomPreset?
    /// IO が止まっている間 (SystemAudioTap.setUp) にだけ変える。残響の AU もここで作り直す
    var sampleRate: Double = 48_000 {
        didSet {
            guard sampleRate != oldValue else { return }
            room?.configure(sampleRate: sampleRate)
            echo.configure(sampleRate: sampleRate)
            commit()
        }
    }

    init() {
        active = .allocate(capacity: Self.paramCount)
        active.initialize(repeating: 0, count: Self.paramCount)
        pending = .allocate(capacity: Self.paramCount)
        pending.initialize(repeating: 0, count: Self.paramCount)
        hasPending = .allocate(capacity: 1)
        hasPending.initialize(to: false)
        state = .allocate(capacity: Self.stateCount)
        state.initialize(repeating: 0, count: Self.stateCount)
        lock = .allocate(capacity: 1)
        lock.initialize(to: os_unfair_lock())
        peak = .allocate(capacity: 1)
        peak.initialize(to: 0)
        ring = .allocate(capacity: Self.ringSize)
        ring.initialize(repeating: 0, count: Self.ringSize)
        ringWriteIndex = .allocate(capacity: 1)
        ringWriteIndex.initialize(to: 0)
        work = .allocate(capacity: RoomReverb.maxFrames * 2)
        work.initialize(repeating: 0, count: RoomReverb.maxFrames * 2)
        roomWasOn = .allocate(capacity: 1)
        roomWasOn.initialize(to: false)
        echoWasOn = .allocate(capacity: 1)
        echoWasOn.initialize(to: false)

        commit()
        active.update(from: pending, count: Self.paramCount)
        hasPending.pointee = false
    }

    deinit {
        active.deallocate()
        pending.deallocate()
        hasPending.deallocate()
        state.deallocate()
        lock.deallocate()
        peak.deallocate()
        ring.deallocate()
        ringWriteIndex.deallocate()
        work.deallocate()
        roomWasOn.deallocate()
        echoWasOn.deallocate()
    }

    // MARK: - メインスレッド

    func update(bands: [EQBand], outputGain: Double, bypass: Bool) {
        self.bands = bands
        self.outputGain = outputGain
        self.bypass = bypass
        commit()
    }

    /// 環境エフェクトの種類と量 (0...1) を変える
    func updateRoom(_ preset: RoomPreset, mix: Double) {
        roomPreset = preset
        if preset != .off {
            if preset != loadedRoomPreset {
                room?.load(preset)
                loadedRoomPreset = preset
            }
            room?.setMix(mix)
        }
        commit()
    }

    func updateEcho(_ settings: EchoSettings) {
        echoSettings = settings
        commit()
    }

    /// 前回呼んでからの入力ピーク (0...1) を返してリセットする
    func consumePeak() -> Float {
        let value = peak.pointee
        peak.pointee = 0
        return value
    }

    /// 直近 count サンプルの EQ 後の出力を dest にコピーする (アナライザー用)。
    /// オーディオスレッドと同時に読み書きするが、表示用なので一部が新旧混ざっても問題にしない
    func copyLatestOutput(into dest: UnsafeMutablePointer<Float>, count: Int) {
        let end = ringWriteIndex.pointee
        let mask = Self.ringSize - 1
        for i in 0..<count {
            dest[i] = ring[(end - count + i) & mask]
        }
    }

    /// 停止中に呼ぶ。次の開始時に前回の残響が出ないようにする
    func resetState() {
        state.update(repeating: 0, count: Self.stateCount)
    }

    private func commit() {
        let sections = bands.flatMap { $0.isOn ? $0.sections(sampleRate: sampleRate) : [] }.prefix(Self.maxSections)

        var params = [Float](repeating: 0, count: Self.paramCount)
        params[0] = Float(sections.count)
        params[1] = Float(pow(10, outputGain / 20))
        params[2] = bypass ? 1 : 0
        params[3] = roomPreset != .off && room != nil ? 1 : 0
        params[4] = echoSettings.isOn ? 1 : 0
        params[5] = Float(echoSettings.time.clamped(to: EchoSettings.timeRange) * sampleRate)
        params[6] = Float(echoSettings.feedback.clamped(to: EchoSettings.feedbackRange))
        params[7] = Float(max(echoSettings.mix, 0))
        params[8] = echoSettings.pingPong ? 1 : 0
        for (i, s) in sections.enumerated() {
            let base = Self.headerCount + i * 5
            params[base] = Float(s.b0)
            params[base + 1] = Float(s.b1)
            params[base + 2] = Float(s.b2)
            params[base + 3] = Float(s.a1)
            params[base + 4] = Float(s.a2)
        }

        os_unfair_lock_lock(lock)
        params.withUnsafeBufferPointer { pending.update(from: $0.baseAddress!, count: Self.paramCount) }
        hasPending.pointee = true
        os_unfair_lock_unlock(lock)
    }

    // MARK: - オーディオスレッド

    private struct Channel {
        var data: UnsafeMutablePointer<Float>
        var stride: Int
    }

    /// input の先頭 skipInputBuffers 個のバッファを飛ばした残りを EQ にかけて output に書く。
    /// 入出力とも Float32 (HAL の IOProc の標準フォーマット) を前提にする
    func process(input: UnsafePointer<AudioBufferList>, skipInputBuffers: Int,
                 output: UnsafeMutablePointer<AudioBufferList>) {
        if os_unfair_lock_trylock(lock) {
            if hasPending.pointee {
                // 段の構成が変わると遅延素子の中身が別のフィルタのものになるので捨てる
                if active[0] != pending[0] || active[2] != pending[2] { resetState() }
                active.update(from: pending, count: Self.paramCount)
                hasPending.pointee = false
            }
            os_unfair_lock_unlock(lock)
        }

        let inBuffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        let outBuffers = UnsafeMutableAudioBufferListPointer(output)

        var outLeft: Channel?, outRight: Channel?
        var outChannelCount = 0
        var outFrames = Int.max
        for buffer in outBuffers {
            guard let data = buffer.mData else { continue }
            memset(data, 0, Int(buffer.mDataByteSize))
            let stride = Int(buffer.mNumberChannels)
            guard stride > 0 else { continue }
            outFrames = min(outFrames, Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * stride))
            for c in 0..<stride {
                let channel = Channel(data: data.assumingMemoryBound(to: Float.self) + c, stride: stride)
                if outChannelCount == 0 { outLeft = channel } else if outChannelCount == 1 { outRight = channel }
                outChannelCount += 1
            }
        }

        var inLeft: Channel?, inRight: Channel?
        var inChannelCount = 0
        var inFrames = Int.max
        if skipInputBuffers < inBuffers.count {
            for b in skipInputBuffers..<inBuffers.count {
                let buffer = inBuffers[b]
                guard let data = buffer.mData else { continue }
                let stride = Int(buffer.mNumberChannels)
                guard stride > 0 else { continue }
                inFrames = min(inFrames, Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * stride))
                for c in 0..<stride where inChannelCount < Self.maxChannels {
                    let channel = Channel(data: data.assumingMemoryBound(to: Float.self) + c, stride: stride)
                    if inChannelCount == 0 { inLeft = channel } else { inRight = channel }
                    inChannelCount += 1
                }
            }
        }

        guard let inL = inLeft, let outL = outLeft else { return }
        let inR = inRight ?? inL
        let frames = min(inFrames, outFrames)

        let sectionCount = Int(active[0])
        let bypassed = active[2] != 0
        let gain = active[1]
        let roomOn = active[3] != 0 && !bypassed
        if roomOn && !roomWasOn.pointee { room?.reset() }
        roomWasOn.pointee = roomOn
        let echoOn = active[4] != 0 && !bypassed
        let echoDelay = active[5], echoFeedback = active[6], echoMix = active[7], pingPong = active[8] != 0
        if echoOn && !echoWasOn.pointee { echo.reset(delaySamples: echoDelay) }
        echoWasOn.pointee = echoOn
        let mask = Self.ringSize - 1
        let left = work, right = work + RoomReverb.maxFrames
        var maxSample: Float = 0

        // 残響の AU に一度に渡せるのは maxFrames までなので、区切って処理する
        var done = 0
        while done < frames {
            let count = min(frames - done, RoomReverb.maxFrames)

            for i in 0..<count {
                let n = done + i
                var l = inL.data[n * inL.stride]
                var r = inR.data[n * inR.stride]
                // NaN や無限大は一度でも遅延素子に入ると以後ずっと無音になるので、入口で無音に置き換える
                if !l.isFinite { l = 0 }
                if !r.isFinite { r = 0 }
                maxSample = max(maxSample, abs(l), abs(r))
                if !bypassed {
                    l = filter(l, channel: 0, sectionCount: sectionCount) * gain
                    r = filter(r, channel: 1, sectionCount: sectionCount) * gain
                    if !l.isFinite || !r.isFinite {
                        resetState()
                        l = 0
                        r = 0
                    }
                }
                left[i] = l
                right[i] = r
            }

            if echoOn {
                echo.process(left: left, right: right, frames: count, delaySamples: echoDelay,
                             feedback: echoFeedback, mix: echoMix, pingPong: pingPong)
            }
            if roomOn, let room {
                room.process(left: left, right: right, frames: count)
            }

            let ringStart = ringWriteIndex.pointee
            for i in 0..<count {
                let n = done + i
                let l = left[i], r = right[i]
                if let outR = outRight {
                    outL.data[n * outL.stride] = l
                    outR.data[n * outR.stride] = r
                } else {
                    outL.data[n * outL.stride] = (l + r) * 0.5
                }
                ring[(ringStart + i) & mask] = (l + r) * 0.5
            }
            ringWriteIndex.pointee = ringStart + count
            done += count
        }

        if maxSample > peak.pointee { peak.pointee = maxSample }
    }

    /// 全段を直列に通す (転置直接形 II)
    @inline(__always)
    private func filter(_ x: Float, channel: Int, sectionCount: Int) -> Float {
        var y = x
        for section in 0..<sectionCount {
            let c = active + Self.headerCount + section * 5
            let z = state + (channel * Self.maxSections + section) * 2
            let input = y
            y = c[0] * input + z[0]
            z[0] = c[1] * input - c[3] * y + z[1]
            z[1] = c[2] * input - c[4] * y
        }
        return y
    }
}
