import ServiceManagement

/// Start-at-login controlled from the UI (not a hand-installed LaunchAgent).
/// SMAppService.mainApp registers the app itself as a login item; the user can
/// toggle it and macOS shows it under System Settings > General > Login Items.
enum LoginItem {
    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static func setEnabled(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}
