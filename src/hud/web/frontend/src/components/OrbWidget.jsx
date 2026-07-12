import { useJarvis } from "../jarvisClient.jsx";

const ACTIVE = new Set([
  "wake_detected", "listening", "transcribing", "thinking", "executing", "speaking",
]);

const TONE = {
  listening: "listen",
  wake_detected: "listen",
  transcribing: "think",
  thinking: "think",
  executing: "think",
  speaking: "speak",
  awaiting_approval: "approve",
  interrupted: "approve",
};

// The ambient desktop orb: a bare, draggable, always-on-top indicator. Pulses
// with the mic level while listening; colour follows the state.
export default function OrbWidget() {
  const { status, connected } = useJarvis();
  const state = status.state || "idle";
  const active = ACTIVE.has(state);
  const tone = TONE[state] || "idle";
  const level = state === "listening" && typeof status.level === "number" ? status.level : 0;
  const scale = 1 + Math.min(1, level) * 0.22;

  function openCommand() {
    try {
      window.pywebview?.api?.show_command();
    } catch (e) {
      /* not running under pywebview (e.g. browser preview) */
    }
  }

  return (
    <div
      className={"orb-widget tone-" + tone}
      title={connected ? "Click to ask Jarvis · drag to move" : "disconnected"}
      onClick={openCommand}
    >
      <div className={"ow-orb " + (active ? "on" : "idle")} style={{ transform: `scale(${scale})` }}>
        <div className="orb-ring" />
        <div className="orb-core" />
      </div>
    </div>
  );
}
