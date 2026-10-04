import SwiftUI

/// ヘッダーの「エコー」から開く設定パネル
struct EchoPanel: View {
    @EnvironmentObject private var eq: EqualizerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Toggle("エコー", isOn: $eq.echo.isOn)
                    .toggleStyle(.switch)
                    .font(.headline)
                Text("実験的")
                    .font(.caption2.bold())
                    .foregroundColor(.black)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color.orange))
                Spacer()
            }

            HStack(spacing: 6) {
                ForEach(EchoSettings.presets, id: \.name) { preset in
                    Button(preset.name) { eq.echo = preset.settings }
                        .buttonStyle(HeaderButtonStyle(isActive: eq.echo == preset.settings))
                }
            }

            row("間隔", value: "\(Int((eq.echo.time * 1000).rounded())) ms") {
                Slider(value: $eq.echo.time, in: EchoSettings.timeRange)
            }
            row("繰り返し", value: "\(Int((eq.echo.feedback * 100).rounded()))%") {
                Slider(value: $eq.echo.feedback, in: EchoSettings.feedbackRange)
            }
            row("量", value: "\(Int((eq.echo.mix * 100).rounded()))%") {
                Slider(value: $eq.echo.mix, in: 0...1)
            }

            Toggle("ピンポン (左右交互に飛ばす)", isOn: $eq.echo.pingPong)

            Text("間隔を変えると、テープエコーのように音程が揺れながら追従します。繰り返すほど音がこもります。")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(width: 340)
    }

    private func row<Control: View>(_ title: String, value: String, @ViewBuilder control: () -> Control) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .frame(width: 56, alignment: .leading)
            control()
            Text(value)
                .monospacedDigit()
                .frame(width: 56, alignment: .trailing)
        }
        .font(.system(size: 12))
        .opacity(eq.echo.isOn ? 1 : 0.5)
    }
}
