import Foundation

enum EQBandType: String, Codable {
    case lowCut, lowShelf, peak, highShelf, highCut

    var hasGain: Bool { self != .lowCut && self != .highCut }
}

/// Logic Pro の Channel EQ と同じ 8 バンド構成の 1 バンド分の設定
struct EQBand: Codable, Equatable {
    var type: EQBandType
    var isOn: Bool
    var frequency: Double
    /// dB (シェルフとピークのみ)
    var gain: Double = 0
    var q: Double = 0.71
    /// dB/Oct (カットのみ)
    var slope: Int = 12

    static let frequencyRange = 20.0...20_000.0
    static let gainRange = -24.0...24.0
    static let qRange = 0.1...12.0
    static let slopes = [6, 12, 18, 24, 36, 48]

    static let defaults: [EQBand] = [
        EQBand(type: .lowCut, isOn: false, frequency: 30, slope: 12),
        EQBand(type: .lowShelf, isOn: true, frequency: 80, q: 0.71),
        EQBand(type: .peak, isOn: true, frequency: 200, q: 1.0),
        EQBand(type: .peak, isOn: true, frequency: 500, q: 1.0),
        EQBand(type: .peak, isOn: true, frequency: 1500, q: 1.0),
        EQBand(type: .peak, isOn: true, frequency: 5000, q: 1.0),
        EQBand(type: .highShelf, isOn: true, frequency: 10_000, q: 0.71),
        EQBand(type: .highCut, isOn: false, frequency: 17_000, slope: 12),
    ]

    /// このバンドを構成する双二次フィルタ。ゲイン 0 など効果がないときは空
    func sections(sampleRate fs: Double) -> [Biquad] {
        let f0 = min(frequency, fs * 0.49)
        switch type {
        case .peak:
            return gain == 0 ? [] : [.peak(frequency: f0, gain: gain, q: q, sampleRate: fs)]
        case .lowShelf:
            return gain == 0 ? [] : [.lowShelf(frequency: f0, gain: gain, q: q, sampleRate: fs)]
        case .highShelf:
            return gain == 0 ? [] : [.highShelf(frequency: f0, gain: gain, q: q, sampleRate: fs)]
        case .lowCut:
            return Biquad.butterworth(highPass: true, frequency: f0, order: slope / 6, sampleRate: fs)
        case .highCut:
            // ナイキスト周波数を超えるローパスは何もしないのと同じ
            return frequency >= fs * 0.49 ? [] : Biquad.butterworth(highPass: false, frequency: f0, order: slope / 6, sampleRate: fs)
        }
    }
}

/// 正規化済み (a0 = 1) の双二次フィルタ係数。式は RBJ Audio EQ Cookbook
struct Biquad {
    var b0: Double, b1: Double, b2: Double, a1: Double, a2: Double

    static func peak(frequency f0: Double, gain: Double, q: Double, sampleRate fs: Double) -> Biquad {
        let a = pow(10, gain / 40)
        let w0 = 2 * Double.pi * f0 / fs
        let alpha = sin(w0) / (2 * q)
        return normalized(b0: 1 + alpha * a, b1: -2 * cos(w0), b2: 1 - alpha * a,
                          a0: 1 + alpha / a, a1: -2 * cos(w0), a2: 1 - alpha / a)
    }

    static func lowShelf(frequency f0: Double, gain: Double, q: Double, sampleRate fs: Double) -> Biquad {
        let a = pow(10, gain / 40)
        let w0 = 2 * Double.pi * f0 / fs
        let c = cos(w0), k = 2 * sqrt(a) * sin(w0) / (2 * q)
        return normalized(b0: a * ((a + 1) - (a - 1) * c + k),
                          b1: 2 * a * ((a - 1) - (a + 1) * c),
                          b2: a * ((a + 1) - (a - 1) * c - k),
                          a0: (a + 1) + (a - 1) * c + k,
                          a1: -2 * ((a - 1) + (a + 1) * c),
                          a2: (a + 1) + (a - 1) * c - k)
    }

    static func highShelf(frequency f0: Double, gain: Double, q: Double, sampleRate fs: Double) -> Biquad {
        let a = pow(10, gain / 40)
        let w0 = 2 * Double.pi * f0 / fs
        let c = cos(w0), k = 2 * sqrt(a) * sin(w0) / (2 * q)
        return normalized(b0: a * ((a + 1) + (a - 1) * c + k),
                          b1: -2 * a * ((a - 1) + (a + 1) * c),
                          b2: a * ((a + 1) + (a - 1) * c - k),
                          a0: (a + 1) - (a - 1) * c + k,
                          a1: 2 * ((a - 1) - (a + 1) * c),
                          a2: (a + 1) - (a - 1) * c - k)
    }

    /// order 次のバターワース (order = スロープ / 6)。2 次の段を重ね、奇数次なら 1 次を 1 段足す
    static func butterworth(highPass: Bool, frequency f0: Double, order: Int, sampleRate fs: Double) -> [Biquad] {
        let w0 = 2 * Double.pi * f0 / fs
        var sections: [Biquad] = []
        for k in 1...max(order / 2, 1) where order >= 2 {
            let q = 1 / (2 * sin(Double(2 * k - 1) * Double.pi / Double(2 * order)))
            let alpha = sin(w0) / (2 * q), c = cos(w0)
            sections.append(highPass
                ? normalized(b0: (1 + c) / 2, b1: -(1 + c), b2: (1 + c) / 2, a0: 1 + alpha, a1: -2 * c, a2: 1 - alpha)
                : normalized(b0: (1 - c) / 2, b1: 1 - c, b2: (1 - c) / 2, a0: 1 + alpha, a1: -2 * c, a2: 1 - alpha))
        }
        if order % 2 == 1 {
            let k = tan(Double.pi * f0 / fs)
            sections.append(highPass
                ? Biquad(b0: 1 / (k + 1), b1: -1 / (k + 1), b2: 0, a1: (k - 1) / (k + 1), a2: 0)
                : Biquad(b0: k / (k + 1), b1: k / (k + 1), b2: 0, a1: (k - 1) / (k + 1), a2: 0))
        }
        return sections
    }

    private static func normalized(b0: Double, b1: Double, b2: Double, a0: Double, a1: Double, a2: Double) -> Biquad {
        Biquad(b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0)
    }

    /// 周波数 f での振幅特性 (dB)
    func magnitudeDB(at f: Double, sampleRate fs: Double) -> Double {
        let w = 2 * Double.pi * f / fs
        let c1 = cos(w), s1 = sin(w), c2 = cos(2 * w), s2 = sin(2 * w)
        let numRe = b0 + b1 * c1 + b2 * c2, numIm = -(b1 * s1 + b2 * s2)
        let denRe = 1 + a1 * c1 + a2 * c2, denIm = -(a1 * s1 + a2 * s2)
        return 10 * log10((numRe * numRe + numIm * numIm) / (denRe * denRe + denIm * denIm))
    }
}
