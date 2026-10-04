import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var audio: AudioEngine

    @State private var showImporter = false
    @State private var isScrubbing = false
    @State private var scrubTime: TimeInterval = 0
    @State private var isDropTargeted = false

    var body: some View {
        VStack(spacing: 16) {
            transportBar
            EQCurveView()
                .frame(height: 140)
            equalizerPanel
            bottomBar
        }
        .padding(20)
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.audio]) { result in
            if case .success(let url) = result {
                audio.load(url: url)
                audio.play()
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first else { return false }
            audio.load(url: url)
            audio.play()
            return true
        } isTargeted: { isDropTargeted = $0 }
        .overlay {
            if isDropTargeted {
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8]))
                    .padding(6)
            }
        }
        .alert("エラー", isPresented: Binding(
            get: { audio.errorMessage != nil },
            set: { if !$0 { audio.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(audio.errorMessage ?? "")
        }
    }

    // MARK: - 再生コントロール

    private var transportBar: some View {
        VStack(spacing: 8) {
            HStack(spacing: 12) {
                Button {
                    showImporter = true
                } label: {
                    Label("開く", systemImage: "folder")
                }
                .keyboardShortcut("o")

                Button {
                    audio.togglePlayPause()
                } label: {
                    Image(systemName: audio.isPlaying ? "pause.fill" : "play.fill")
                        .frame(width: 20)
                }
                .keyboardShortcut(.space, modifiers: [])
                .disabled(!audio.hasFile)

                Button {
                    audio.stop()
                } label: {
                    Image(systemName: "stop.fill")
                        .frame(width: 20)
                }
                .keyboardShortcut(".")
                .disabled(!audio.hasFile)

                Text(audio.fileName ?? "音声ファイルを開くか、ここにドロップしてください")
                    .font(.headline)
                    .foregroundColor(audio.fileName == nil ? .secondary : .primary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "speaker.fill")
                    .foregroundColor(.secondary)
                Slider(value: $audio.volume, in: 0...1)
                    .frame(width: 110)
                Image(systemName: "speaker.wave.3.fill")
                    .foregroundColor(.secondary)
            }

            HStack(spacing: 8) {
                Text(formatTime(isScrubbing ? scrubTime : audio.currentTime))
                    .monospacedDigit()
                    .frame(width: 48, alignment: .trailing)
                Slider(
                    value: Binding(
                        get: { isScrubbing ? scrubTime : audio.currentTime },
                        set: { scrubTime = $0 }
                    ),
                    in: 0...max(audio.duration, 0.01)
                ) { editing in
                    if editing {
                        scrubTime = audio.currentTime
                        isScrubbing = true
                    } else {
                        audio.seek(to: scrubTime)
                        isScrubbing = false
                    }
                }
                .disabled(!audio.hasFile)
                Text(formatTime(audio.duration))
                    .monospacedDigit()
                    .frame(width: 48, alignment: .leading)
            }
            .font(.caption)
            .foregroundColor(.secondary)
        }
    }

    // MARK: - イコライザー

    private var equalizerPanel: some View {
        HStack(alignment: .top, spacing: 0) {
            bandColumn(
                title: "PRE",
                value: $audio.preamp
            )
            .padding(.trailing, 12)

            Divider()
                .padding(.trailing, 12)

            ForEach(AudioEngine.frequencies.indices, id: \.self) { index in
                bandColumn(
                    title: frequencyLabel(AudioEngine.frequencies[index]),
                    value: Binding(
                        get: { audio.gains[index] },
                        set: { audio.setGain($0, forBand: index) }
                    )
                )
                .frame(maxWidth: .infinity)
            }

            // dB 目盛り
            VStack {
                Text("+12 dB")
                Spacer()
                Text("0 dB")
                Spacer()
                Text("-12 dB")
            }
            .font(.system(size: 9))
            .foregroundColor(.secondary)
            .padding(.vertical, 22)
            .padding(.leading, 8)
        }
        .frame(maxHeight: .infinity)
        .opacity(audio.isBypassed ? 0.5 : 1)
    }

    private func bandColumn(title: String, value: Binding<Float>) -> some View {
        VStack(spacing: 6) {
            Text(String(format: "%+.1f", value.wrappedValue))
                .font(.caption2)
                .monospacedDigit()
                .foregroundColor(.secondary)
            VerticalSlider(value: value, range: AudioEngine.gainRange)
                .frame(width: 28)
            Text(title)
                .font(.caption)
                .bold()
        }
        .frame(minWidth: 44)
    }

    // MARK: - プリセット / バイパス

    private var bottomBar: some View {
        HStack(spacing: 12) {
            Menu {
                ForEach(EQPreset.all) { preset in
                    Button(preset.name) { audio.apply(preset) }
                }
            } label: {
                Label("プリセット: \(currentPresetName)", systemImage: "slider.vertical.3")
            }
            .fixedSize()

            Button("リセット") {
                audio.resetEQ()
            }

            Spacer()

            Toggle("EQ バイパス", isOn: $audio.isBypassed)
                .toggleStyle(.switch)
        }
    }

    // MARK: - ヘルパー

    private var currentPresetName: String {
        EQPreset.all.first { $0.gains == audio.gains }?.name ?? "カスタム"
    }

    private func frequencyLabel(_ f: Float) -> String {
        f >= 1000 ? "\(Int(f / 1000))k" : "\(Int(f))"
    }

    private func formatTime(_ t: TimeInterval) -> String {
        guard t.isFinite, t >= 0 else { return "0:00" }
        let total = Int(t)
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

#Preview {
    ContentView()
        .environmentObject(AudioEngine())
        .frame(width: 800, height: 600)
}
