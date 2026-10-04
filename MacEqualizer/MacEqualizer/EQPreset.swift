import Foundation

/// 10バンドイコライザーのプリセット
/// 各値は AudioEngine.frequencies と同じ順番のゲイン (dB)
struct EQPreset: Identifiable, Hashable {
    let name: String
    let gains: [Float]

    var id: String { name }

    static let all: [EQPreset] = [
        EQPreset(name: "フラット",        gains: [ 0,  0,  0,  0,  0,  0,  0,  0,  0,  0]),
        EQPreset(name: "低音強調",        gains: [ 6,  5,  4,  2,  0,  0,  0,  0,  0,  0]),
        EQPreset(name: "低音カット",      gains: [-8, -6, -4, -2,  0,  0,  0,  0,  0,  0]),
        EQPreset(name: "高音強調",        gains: [ 0,  0,  0,  0,  0,  1,  2,  4,  5,  6]),
        EQPreset(name: "ボーカル",        gains: [-2, -2, -1,  1,  3,  4,  3,  1,  0, -1]),
        EQPreset(name: "ロック",          gains: [ 5,  4,  2, -1, -2, -1,  1,  3,  4,  5]),
        EQPreset(name: "ポップ",          gains: [-1,  1,  3,  4,  3,  0, -1, -1,  1,  2]),
        EQPreset(name: "ジャズ",          gains: [ 3,  2,  1,  2, -1, -1,  0,  1,  2,  3]),
        EQPreset(name: "クラシック",      gains: [ 4,  3,  2,  1, -1, -1,  0,  2,  3,  4]),
        EQPreset(name: "エレクトロニック", gains: [ 5,  4,  1,  0, -2,  1,  0,  1,  4,  5]),
        EQPreset(name: "ラウドネス",      gains: [ 6,  4,  0,  0, -2,  0, -1, -3,  4,  2]),
    ]
}
