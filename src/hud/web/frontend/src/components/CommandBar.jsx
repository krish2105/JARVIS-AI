import { useEffect, useRef, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";
import Markdown from "./Markdown.jsx";
import CopyButton from "./CopyButton.jsx";
import { ResultCards } from "./ResultCard.jsx";

// Spotlight-style command bar: summon anywhere, type or talk, answer streams
// inline. Rendered in its own transparent always-on-top window (index.html#command).
export default function CommandBar() {
  const { sendCommand, connected } = useJarvis();
  const [q, setQ] = useState("");
  const [reply, setReply] = useState("");
  const [cards, setCards] = useState([]);
  const [busy, setBusy] = useState(false);
  const [asked, setAsked] = useState("");
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

  function run(text) {
    if (!text || busy) return;
    setAsked(text);
    setReply("");
    setCards([]);
    setBusy(true);
    sendCommand(text, (chunk) => {
      setReply(chunk.reply || "");
      if (chunk.done) {
        if (Array.isArray(chunk.cards)) setCards(chunk.cards);
        setBusy(false);
      }
    });
  }

  function submit(e) {
    e.preventDefault();
    run(q.trim());
  }

  function retry() {
    if (asked) run(asked);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (reply || busy) {
        setReply("");
        setCards([]);
        setBusy(false);
        setQ("");
        setAsked("");
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
          {reply ? <Markdown>{reply}</Markdown> : "Thinking…"}
          {busy && <span className="cmd-caret" />}
          <ResultCards cards={cards} />
          {!busy && reply && (
            <div className="cmd-act">
              <CopyButton text={reply} />
              <button className="copybtn" title="Ask again" onClick={retry}>↻ Retry</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
