import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

// The context bar: a strip of chips showing what Jarvis is working with right
// now — the active model, how many memories it can draw on, a live clock — and
// the composer toggles (currently: attach the screen for local vision). The
// `attachments` object + `onToggle` are owned by the composer above it.
export default function ContextBar({ attachments = {}, onToggle }) {
  const { request, connected } = useJarvis();
  const [snap, setSnap] = useState(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    if (!connected) return;
    let alive = true;
    const load = () => request("context_snapshot").then((r) => alive && !r?.error && setSnap(r));
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [connected, request]);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  const clock = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  return (
    <div className="ctxbar">
      <button
        className={"ctx-chip toggle" + (attachments.screen ? " on" : "")}
        onClick={() => onToggle && onToggle("screen")}
        title="Attach your screen — Jarvis answers with local vision"
      >
        <span className="ctx-ico">▢</span> Screen{attachments.screen ? " ✓" : ""}
      </button>
      {snap && (
        <>
          <span className="ctx-chip"><span className="ctx-ico">✦</span> {snap.model}</span>
          <span className="ctx-chip"><span className="ctx-ico">🧠</span> {snap.memories} {snap.memories === 1 ? "memory" : "memories"}</span>
        </>
      )}
      <span className="ctx-chip"><span className="ctx-ico">🕐</span> {clock}</span>
      <span className={"ctx-chip dotwrap"}>
        <i className={"ctx-dot " + (connected ? "up" : "down")} />
        {connected ? "live" : "offline"}
      </span>
    </div>
  );
}
