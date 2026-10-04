import SwiftUI

/// Logic Pro のプラグイン画面に寄せた配色
enum Theme {
    static let windowBackground = Color(red: 0.11, green: 0.12, blue: 0.13)
    static let header = Color(red: 0.17, green: 0.18, blue: 0.20)
    static let panel = Color(red: 0.15, green: 0.16, blue: 0.18)
    static let graphBackground = Color(red: 0.06, green: 0.07, blue: 0.08)
    static let gridMinor = Color.white.opacity(0.045)
    static let gridMajor = Color.white.opacity(0.11)
    static let zeroLine = Color.white.opacity(0.22)
    static let label = Color(red: 0.58, green: 0.61, blue: 0.65)
    static let text = Color(red: 0.86, green: 0.88, blue: 0.90)
    static let curve = Color(red: 0.94, green: 0.96, blue: 0.98)
    static let accent = Color(red: 0.32, green: 0.64, blue: 1.0)

    static let analyzerFillTop = Color(red: 0.36, green: 0.53, blue: 0.70).opacity(0.55)
    static let analyzerFillBottom = Color(red: 0.24, green: 0.36, blue: 0.50).opacity(0.10)
    static let analyzerLine = Color(red: 0.56, green: 0.74, blue: 0.90).opacity(0.85)
    static let analyzerLabel = Color(red: 0.46, green: 0.63, blue: 0.80)

    /// Low Cut, Low Shelf, Peak 1〜4, High Shelf, High Cut (RGB)。ビジュアル表示でも低音→高音の色として使う
    static let bandRGB: [(red: Double, green: Double, blue: Double)] = [
        (0.91, 0.30, 0.32),
        (0.96, 0.56, 0.21),
        (0.96, 0.81, 0.28),
        (0.46, 0.83, 0.37),
        (0.26, 0.80, 0.76),
        (0.33, 0.57, 0.98),
        (0.63, 0.45, 0.96),
        (0.90, 0.38, 0.72),
    ]
    static let bandColors: [Color] = bandRGB.map { Color(red: $0.red, green: $0.green, blue: $0.blue) }
}

extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
