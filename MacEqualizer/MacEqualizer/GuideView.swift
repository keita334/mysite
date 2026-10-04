import SwiftUI

/// 「説明」ウィンドウ。内容は FeatureGuide から作る
struct GuideView: View {
    static let windowID = "guide"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("MacEqualizer の説明")
                        .font(.system(size: 24, weight: .bold))
                    Text(LocalizedStringKey(FeatureGuide.intro))
                        .foregroundColor(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }

                section(symbol: "point.3.connected.trianglepath.dotted", title: "音の流れ", color: Theme.accent) {
                    SignalFlowView()
                        .padding(.bottom, 4)
                    bullets(FeatureGuide.flowSteps)
                }

                ForEach(FeatureGuide.sections) { guide in
                    section(symbol: guide.symbol, title: guide.title, badge: guide.badge, color: guide.color) {
                        bullets(guide.items)
                        if guide.showsPrivacySettingsButton {
                            Button("権限の設定を開く") { PrivacySettings.openAudioCapture() }
                                .buttonStyle(HeaderButtonStyle())
                                .padding(.leading, 18)
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .foregroundColor(Theme.text)
        .background(Theme.windowBackground)
    }

    private func section<Content: View>(symbol: String, title: String, badge: String? = nil, color: Color,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(color)
                    .frame(width: 28, height: 28)
                    .background(RoundedRectangle(cornerRadius: 7).fill(color.opacity(0.16)))
                Text(title)
                    .font(.system(size: 17, weight: .semibold))
                if let badge {
                    Text(badge)
                        .font(.caption2.bold())
                        .foregroundColor(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(Color.orange))
                }
            }
            content()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Theme.panel))
    }

    private func bullets(_ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(items, id: \.self) { item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("•").foregroundColor(Theme.label)
                    // 文字列の中の **太字** を Markdown として表示する
                    Text(LocalizedStringKey(item))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: 13))
            }
        }
        .padding(.leading, 4)
    }
}

/// 音の流れの図: アプリの音 → 取り込み → EQ → エコー → 環境 → 出力、その後ろでアナライザー → 表示
struct SignalFlowView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 4) {
                node("music.note.list", "アプリの音", "Apple Music など", Theme.bandColors[0])
                arrow
                node("tray.and.arrow.down", "取り込み", "Process Tap", Theme.bandColors[1])
                arrow
                node("slider.horizontal.3", "EQ", "8 バンド", Theme.bandColors[5])
                arrow
                node("repeat", "エコー", "実験的", Theme.bandColors[2])
                arrow
                node("building.columns", "環境", "響き", Theme.bandColors[6])
                arrow
                node("hifispeaker.2", "出力", "スピーカーなど", Theme.bandColors[3])
            }
            HStack(alignment: .top, spacing: 4) {
                Image(systemName: "arrow.turn.down.right")
                    .foregroundColor(Theme.label)
                    .frame(width: 28, height: 44)
                Text("出力した音を分析")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.label)
                    .frame(width: 92, height: 44, alignment: .leading)
                node("chart.bar.xaxis", "アナライザー", "FFT", Theme.analyzerLabel)
                arrow
                node("sparkles", "グラフ / ビジュアル", "画面に表示", Theme.bandColors[7])
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.black.opacity(0.3)))
    }

    private var arrow: some View {
        Image(systemName: "arrow.right")
            .font(.system(size: 11, weight: .bold))
            .foregroundColor(Theme.label)
            .frame(width: 14, height: 44)
    }

    private func node(_ symbol: String, _ title: String, _ subtitle: String, _ color: Color) -> some View {
        VStack(spacing: 4) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(color)
                .frame(width: 44, height: 44)
                .background(RoundedRectangle(cornerRadius: 10).fill(color.opacity(0.16)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.5)))
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .multilineTextAlignment(.center)
            Text(subtitle)
                .font(.system(size: 10))
                .foregroundColor(Theme.label)
        }
        .frame(width: 70)
    }
}
