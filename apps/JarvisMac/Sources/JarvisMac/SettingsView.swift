import SwiftUI

/// Settings shell. Reads/writes config.yaml through the worker's existing RPC
/// (src/hud/api.py get_config/save_config) over the same WebSocket. Only the
/// server-side editable allowlist is honored, and the server re-adds mandatory
/// confirmations, so the UI cannot weaken the safety model (see the audit).
///
/// STATUS: scaffold — the tabs below are placeholders for the full surfaces
/// named in docs/IMPLEMENTATION_STATUS.md (Permissions, Memory, History,
/// Diagnostics, Models, Voice).
struct SettingsView: View {
    @ObservedObject var bridge: WorkerBridge

    var body: some View {
        TabView {
            Text("General settings (wake word, voice) — TODO")
                .tabItem { Label("General", systemImage: "gearshape") }
            Text("Permissions center — TODO")
                .tabItem { Label("Permissions", systemImage: "lock.shield") }
            Text("Memory viewer / export / reset — TODO")
                .tabItem { Label("Memory", systemImage: "brain") }
            Text("Diagnostics / logs — TODO")
                .tabItem { Label("Diagnostics", systemImage: "stethoscope") }
        }
        .frame(width: 480, height: 360)
        .padding()
    }
}
