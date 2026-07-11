import Foundation

/// Mirrors the Python voice worker's state by consuming the same local
/// WebSocket the React HUD uses (ws://127.0.0.1:8765). This is a consumer-only
/// client: it sends no state, matching src/hud/client.py's role split. The
/// server's Origin allowlist (src/hud/server.py) permits this native client
/// because URLSessionWebSocketTask sends no browser Origin header.
@MainActor
final class WorkerBridge: ObservableObject {
    enum VoiceState: String {
        case stopped, initializing, idle, wakeDetected = "wake_detected"
        case listening, transcribing, thinking, awaitingApproval = "awaiting_approval"
        case executing, speaking, interrupted, error, degraded

        var symbolName: String {
            switch self {
            case .idle, .stopped: return "moon.zzz"
            case .listening, .wakeDetected: return "mic.fill"
            case .transcribing, .thinking: return "brain"
            case .awaitingApproval: return "hand.raised.fill"
            case .executing: return "gearshape.fill"
            case .speaking: return "speaker.wave.2.fill"
            case .interrupted: return "hand.raised.slash"
            case .initializing: return "hourglass"
            case .error, .degraded: return "exclamationmark.triangle.fill"
            }
        }
    }

    @Published var state: VoiceState = .stopped
    @Published var transcript: String = ""
    @Published var reply: String = ""
    @Published var connected: Bool = false

    private var task: URLSessionWebSocketTask?
    private let url = URL(string: "ws://127.0.0.1:8765")!

    func connect() {
        task = URLSession.shared.webSocketTask(with: url)
        task?.resume()
        connected = true
        receive()
    }

    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure:
                Task { @MainActor in
                    self.connected = false
                    // Reconnect after a short delay — the worker may still be booting.
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    self.connect()
                }
            case .success(let message):
                if case let .string(text) = message { self.handle(text) }
                self.receive()
            }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stateStr = obj["state"] as? String else { return }
        Task { @MainActor in
            self.state = VoiceState(rawValue: stateStr) ?? .idle
            self.transcript = obj["transcript"] as? String ?? self.transcript
            self.reply = obj["reply"] as? String ?? self.reply
        }
    }
}
