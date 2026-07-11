// swift-tools-version: 5.9
// NOTE: This scaffold has NOT been compiled in the audit environment (no
// Xcode / Swift toolchain available there). Open in Xcode 15+ on macOS 14+ to
// build, run, and verify. It is a correct-by-construction starting point for
// the native shell that replaces the three LaunchAgents — not a finished app.
import PackageDescription

let package = Package(
    name: "JarvisMac",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "JarvisMac",
            path: "Sources/JarvisMac"
        )
    ]
)
