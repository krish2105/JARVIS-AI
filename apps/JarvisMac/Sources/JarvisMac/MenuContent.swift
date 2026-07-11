import AppKit
import SwiftUI

/// The menu-bar dropdown. Minimal on purpose — the full Settings/Permissions/
/// History/Memory/Diagnostics surfaces live in the Settings window; the menu is
/// for status + the few one-click controls.
struct MenuContent: View {
    @ObservedObject var bridge: WorkerBridge
    @ObservedObject var worker: WorkerSupervisor
    @State private var launchAtLogin = LoginItem.isEnabled

    var body: some View {
        Text("Jarvis — \(bridge.state.rawValue)")
        if !bridge.reply.isEmpty {
            Text(bridge.reply).lineLimit(2)
        }
        Divider()

        Text(worker.running ? "Worker: running" : "Worker: stopped")
        Button(worker.running ? "Restart worker" : "Start worker") {
            worker.stopAll(); worker.start()
        }

        Toggle("Start at login", isOn: $launchAtLogin)
            .onChange(of: launchAtLogin) { _, newValue in
                try? LoginItem.setEnabled(newValue)
            }

        Divider()
        SettingsLink { Text("Settings…") }

        Button("Quit Jarvis (stops everything)") {
            worker.stopAll()
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q")
    }
}
