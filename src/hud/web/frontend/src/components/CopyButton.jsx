import { useState } from "react";

// Small copy-to-clipboard affordance shown on message hover.
export default function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  function copy() {
    if (!text) return;
    try {
      navigator.clipboard?.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  }
  return (
    <button className="copybtn" title={label} onClick={copy}>
      {done ? "✓ Copied" : "⧉ " + label}
    </button>
  );
}
