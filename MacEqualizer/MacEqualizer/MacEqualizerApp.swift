import SwiftUI

@main
struct MacEqualizerApp: App {
    @StateObject private var audio = AudioEngine()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(audio)
                .frame(minWidth: 760, minHeight: 560)
        }
        .windowResizability(.contentMinSize)
    }
}
