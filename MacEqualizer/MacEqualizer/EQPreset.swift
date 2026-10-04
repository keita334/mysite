import Foundation

/// プリセット。周波数と Q は初期値のまま、ゲインとローカットだけを変える
struct EQPreset: Identifiable {
    let name: String
    /// ローシェルフ, ピーク 1〜4, ハイシェルフのゲイン (dB)
    let gains: [Double]
    /// ローカットを使う場合の周波数
    var lowCut: Double?

    var id: String { name }

    var bands: [EQBand] {
        var bands = EQBand.defaults
        for (index, gain) in zip(1...6, gains) {
            bands[index].gain = gain
        }
        if let lowCut {
            bands[0].isOn = true
            bands[0].frequency = lowCut
            bands[0].slope = 24
        }
        return bands
    }

    static let all: [EQPreset] = [
        EQPreset(name: "フラット",          gains: [ 0,  0,  0,  0,  0,  0]),
        EQPreset(name: "低音強調",          gains: [ 6,  2,  0,  0,  0,  0]),
        EQPreset(name: "低音カット",        gains: [ 0,  0,  0,  0,  0,  0], lowCut: 100),
        EQPreset(name: "高音強調",          gains: [ 0,  0,  0,  0,  2,  6]),
        EQPreset(name: "ドンシャリ",        gains: [ 6,  0, -4, -3,  0,  6]),
        EQPreset(name: "ボーカル",          gains: [-2, -1,  1,  3,  2,  0]),
        EQPreset(name: "ロック",            gains: [ 4,  1, -2,  0,  2,  4]),
        EQPreset(name: "ポップ",            gains: [ 1,  2,  1,  2,  1,  2]),
        EQPreset(name: "ジャズ",            gains: [ 3,  1, -1,  1,  2,  3]),
        EQPreset(name: "クラシック",        gains: [ 3,  0, -1,  0,  2,  4]),
        EQPreset(name: "声を聞きやすく",    gains: [ 0, -2,  0,  4,  2, -2], lowCut: 120),
    ]
}
