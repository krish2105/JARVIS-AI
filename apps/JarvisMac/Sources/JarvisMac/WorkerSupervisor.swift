import Foundation

/// Supervises the bundled Python voice worker (src.pipeline) as a child
/// process, so the user never runs a terminal command or installs LaunchAgents.
/// In a shipped .app the Python runtime + venv are embedded under
/// Contents/Resources; this points at that embedded interpreter.
///
/// STATUS: scaffold. The embedded-runtime packaging is the remaining work —
/// see docs/RELEASE_PLAN.md. Paths below assume that packaging exists.
@MainActor
final class WorkerSupervisor: ObservableObject {
    @Published var running = false
    private var process: Process?

    private var embeddedPython: URL? {
        Bundle.main.url(forResource: "python3", withExtension: nil, subdirectory: "runtime/bin")
    }

    private var workerRoot: URL? {
        Bundle.main.url(forResource: "jarvis", withExtension: nil, subdirectory: "worker")
    }

    func start() {
        guard process == nil, let python = embeddedPython, let root = workerRoot else { return }
        let proc = Process()
        proc.executableURL = python
        proc.arguments = ["-m", "src.pipeline"]
        proc.currentDirectoryURL = root
        proc.terminationHandler = { [weak self] _ in
            Task { @MainActor in
                self?.running = false
                // Bounded auto-restart could go here; keep manual for the scaffold.
            }
        }
        do {
            try proc.run()
            process = proc
            running = true
        } catch {
            running = false
        }
    }

    /// Full quit: stop every child process. Bound to the menu's Quit item so
    /// "Quit" really stops the worker, not just the UI (a P0 UX requirement).
    func stopAll() {
        process?.terminate()
        process = nil
        running = false
    }
}
