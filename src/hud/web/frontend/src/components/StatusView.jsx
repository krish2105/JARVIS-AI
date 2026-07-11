import { useJarvis } from "../jarvisClient.jsx";

export default function StatusView() {
  const { status } = useJarvis();
  const state = status.state || "idle";

  let caption = "";
  if (state === "thinking") caption = status.transcript || "";
  else if (state === "speaking") caption = status.reply || "";

  return (
    <div className="status-view">
      <div className="ring-wrap" data-state={state}>
        <svg viewBox="0 0 120 120">
          <circle className="ring-bg" cx="60" cy="60" r="52" />
          <circle className="ring-fg" cx="60" cy="60" r="52" strokeDasharray="220 327" />
        </svg>
        <div className="core" />
      </div>
      <div className="label">{state}</div>
      <div className="caption">{caption}</div>
      {state === "idle" && (
        <div className="hint">Say your wake word to start listening.</div>
      )}
    </div>
  );
}
