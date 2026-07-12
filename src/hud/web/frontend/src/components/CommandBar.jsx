import { useEffect, useRef, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

// Spotlight-style command bar: summon anywhere, type or talk, answer streams
// inline. Rendered in its own transparent always-on-top window (index.html#command).
export default function CommandBar() {
  const { sendCommand, connected } = useJarvis();
  const [q, setQ] = useState("");
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function close() {
    try {
      window.pywebview?.api?.hide_command();
    } catch (e) {
      /* not under pywebview */
    }
  }

  function submit(e) {
    e.preventDefault();
    const text = q.trim();
    if (!text || busy) return;
    setReply("");
    setBusy(true);
    sendCommand(text, (chunk) => {
      setReply(chunk.reply || "");
      if (chunk.done) setBusy(false);
    });
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (reply || busy) {
        setReply("");
        setBusy(false);
        setQ("");
      } else {
        close();
      }
    }
  }

  return (
    <div className="cmd-wrap" onKeyDown={onKeyDown}>
      <form className={"cmd-bar" + (busy ? " busy" : "")} onSubmit={submit}>
        <span className={"cmd-dot" + (busy ? " spin" : "")} />
        <input
          ref={inputRef}
          className="cmd-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={connected ? "Ask Jarvis…" : "Connecting…"}
          spellCheck={false}
          autoComplete="off"
        />
        <kbd className="cmd-kbd">{busy ? "…" : "⏎"}</kbd>
      </form>
      {(reply || busy) && (
        <div className="cmd-result">
          {reply || "Thinking…"}
          {busy && <span className="cmd-caret" />}
        </div>
      )}
    </div>
  );
}
