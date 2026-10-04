import SwiftUI

@main
struct MacEqualizerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var eq = EqualizerModel()

    var body: some Scene {
        Window("MacEqualizer", id: "main") {
            ContentView()
                .environmentObject(eq)
                .frame(minWidth: 980, minHeight: 580)
                .preferredColorScheme(.dark)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .help) {
                GuideMenuButton()
            }
        }

        Window("MacEqualizer の説明", id: GuideView.windowID) {
            GuideView()
                .frame(minWidth: 600, minHeight: 480)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 700, height: 760)
    }
}

/// メニューバーの「ヘルプ」から説明を開く (⌘?)
private struct GuideMenuButton: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("MacEqualizer の説明") { openWindow(id: GuideView.windowID) }
            .keyboardShortcut("?", modifiers: .command)
    }
}

/// ウィンドウを閉じたらアプリも終了する。EQ が見えないまま掛かり続けないようにするため (終了すると元の音に戻る)
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
