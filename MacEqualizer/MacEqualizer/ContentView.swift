import SwiftUI

enum DisplayMode: String {
    case equalizer, visual
}

struct ContentView: View {
    @EnvironmentObject private var eq: EqualizerModel
    @State private var selectedBand: Int?
    @AppStorage("ui.displayMode") private var displayMode = DisplayMode.equalizer
    @State private var showsEchoPanel = false
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            headerBar
            switch displayMode {
            case .equalizer:
                VStack(spacing: 8) {
                    bandToggleRow
                    EQGraphView(selectedBand: $selectedBand)
                    BandParameterGrid(selectedBand: $selectedBand)
                }
                .padding(12)
            case .visual:
                ParticleVisualizerView(analyzer: eq.analyzer)
            }
        }
        .background(Theme.windowBackground)
        .alert("エラー", isPresented: Binding(
            get: { eq.errorMessage != nil },
            set: { if !$0 { eq.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(eq.errorMessage ?? "")
        }
    }

    // MARK: - ヘッダー

    private var headerBar: some View {
        HStack(spacing: 8) {
            Button {
                eq.isBypassed.toggle()
            } label: {
                Image(systemName: "power")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(eq.isBypassed ? Theme.label : .black)
                    .frame(width: 22, height: 22)
                    .background(Circle().fill(eq.isBypassed ? Theme.panel : Theme.accent))
                    .overlay(Circle().stroke(Color.black.opacity(0.5)))
            }
            .buttonStyle(.plain)
            .help(eq.isBypassed ? "EQ をオンにする" : "EQ をオフにする (バイパス)")

            HStack(spacing: 2) {
                displayModeButton("EQ", mode: .equalizer)
                displayModeButton("ビジュアル", mode: .visual)
            }

            Menu {
                ForEach(EQPreset.all) { preset in
                    Button(preset.name) { eq.apply(preset) }
                }
            } label: {
                Text(eq.currentPresetName)
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .padding(.horizontal, 8)
            .frame(height: 22)
            .background(RoundedRectangle(cornerRadius: 4).fill(Color.black.opacity(0.35)))
            .help("プリセット")

            Button("リセット") { eq.reset() }
                .buttonStyle(HeaderButtonStyle())
                .help("EQ を初期状態に戻す (環境エフェクトはそのまま)")

            roomControls

            Button("エコー") { showsEchoPanel.toggle() }
                .buttonStyle(HeaderButtonStyle(isActive: eq.echo.isOn))
                .popover(isPresented: $showsEchoPanel, arrowEdge: .bottom) {
                    EchoPanel().environmentObject(eq)
                }
                .help("エコー (実験的) の設定を開く")

            if displayMode == .equalizer {
                AnalyzerToggle(analyzer: eq.analyzer)
            }

            Spacer()

            statusView

            Button {
                openWindow(id: GuideView.windowID)
            } label: {
                Image(systemName: "book")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.text)
                    .frame(width: 26, height: 22)
                    .background(RoundedRectangle(cornerRadius: 4).fill(Color.white.opacity(0.08)))
                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.black.opacity(0.45)))
            }
            .buttonStyle(.plain)
            .help("機能の説明を開く (⌘?)")
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(Theme.header)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Color.black.opacity(0.6)).frame(height: 1)
        }
    }

    /// 環境エフェクト: 空間の種類と量
    private var roomControls: some View {
        HStack(spacing: 4) {
            Menu {
                Picker("環境", selection: Binding(get: { eq.room }, set: { eq.selectRoom($0) })) {
                    ForEach(RoomPreset.allCases) { preset in
                        Text(preset.name).tag(preset)
                    }
                }
                .pickerStyle(.inline)
                .labelsHidden()
            } label: {
                Text("環境: \(eq.room.name)")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .padding(.horizontal, 8)
            .frame(height: 22)
            .background(RoundedRectangle(cornerRadius: 4).fill(Color.black.opacity(0.35)))
            .help("ホールやスタジオなどの響きを付ける")

            if eq.room != .off {
                DragValueField(text: "\(Int((eq.roomMix * 100).rounded()))%") { delta in
                    eq.roomMix = ((eq.roomMix + delta * 0.005) * 100).rounded() / 100
                    eq.roomMix = eq.roomMix.clamped(to: 0...1)
                } onReset: {
                    eq.roomMix = eq.room.defaultMix
                }
                .frame(width: 48)
                .help("響きの量。上下にドラッグで調整 / ダブルクリックでその空間のおすすめ値")
            }
        }
    }

    private func displayModeButton(_ title: String, mode: DisplayMode) -> some View {
        Button(title) { displayMode = mode }
            .buttonStyle(HeaderButtonStyle(isActive: displayMode == mode))
    }

    @ViewBuilder
    private var statusView: some View {
        if eq.isRunning {
            HStack(spacing: 6) {
                Circle().fill(Color.green).frame(width: 7, height: 7)
                Text("出力: \(eq.outputDeviceName ?? "-")")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.label)
                    .lineLimit(1)
            }
        } else {
            HStack(spacing: 6) {
                Circle().fill(Color.red).frame(width: 7, height: 7)
                Text("停止中")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.label)
                Button("再開") { eq.start() }
                    .buttonStyle(HeaderButtonStyle())
                Button("権限の設定…") { PrivacySettings.openAudioCapture() }
                    .buttonStyle(HeaderButtonStyle())
            }
        }
    }

    // MARK: - バンドのオン/オフ

    private var bandToggleRow: some View {
        HStack(spacing: BandLayout.spacing) {
            Color.clear.frame(width: BandLayout.labelColumnWidth, height: 1)
            ForEach(eq.bands.indices, id: \.self) { index in
                BandToggleButton(
                    band: $eq.bands[index],
                    color: Theme.bandColors[index],
                    isSelected: selectedBand == index
                ) {
                    selectedBand = index
                }
            }
            Color.clear.frame(width: BandLayout.outputColumnWidth, height: 1)
        }
    }
}

/// アナライザーの表示切り替え。アナライザーの状態だけを監視する
private struct AnalyzerToggle: View {
    @ObservedObject var analyzer: SpectrumAnalyzer

    var body: some View {
        Button("アナライザー") { analyzer.isEnabled.toggle() }
            .buttonStyle(HeaderButtonStyle(isActive: analyzer.isEnabled))
            .help("EQ 後の出力のスペクトルをグラフの後ろに表示")
    }
}

struct HeaderButtonStyle: ButtonStyle {
    var isActive = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(isActive ? .black : Theme.text)
            .padding(.horizontal, 10)
            .frame(height: 22)
            .background(RoundedRectangle(cornerRadius: 4).fill(
                isActive ? Theme.accent : Color.white.opacity(configuration.isPressed ? 0.2 : 0.08)))
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.black.opacity(0.45)))
    }
}

#Preview {
    ContentView()
        .environmentObject(EqualizerModel(startsAudio: false))
        .frame(width: 1000, height: 620)
        .preferredColorScheme(.dark)
}
