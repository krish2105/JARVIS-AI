import SwiftUI

// The native menu-bar shell for Jarvis. Its job is to replace the three
// LaunchAgents + terminal setup with a single normal Mac app:
//   • it owns the menu bar (state, pause, quit-everything)
//   • it supervises the Python voice worker as a child process
//   • it mirrors the worker's state over the local WebSocket (WorkerBridge)
//   • it manages start-at-login via SMAppService (LoginItem)
//
// STATUS: scaffold, not yet built/verified. See apps/JarvisMac/README.md.
@main
struct JarvisMacApp: App {
    @StateObject private var bridge = WorkerBridge()
    @StateObject private var worker = WorkerSupervisor()

    var body: some Scene {
        MenuBarExtra {
            MenuContent(bridge: bridge, worker: worker)
        } label: {
            Image(systemName: bridge.state.symbolName)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView(bridge: bridge)
        }
    }

    init() {
        worker.start()
        bridge.connect()
    }
}
