import { useEffect, useRef, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";
import Markdown from "./Markdown.jsx";
import CopyButton from "./CopyButton.jsx";
import { ResultCards } from "./ResultCard.jsx";
import ContextBar from "./ContextBar.jsx";
import AgentTrace from "./AgentTrace.jsx";

// Spotlight-style command bar: summon anywhere, type or talk, answer streams
// inline. Rendered in its own transparent always-on-top window (index.html#command).
export default function CommandBar() {
  const { sendCommand, connected } = useJarvis();
  const [q, setQ] = useState("");
  const [reply, setReply] = useState("");
  const [cards, setCards] = useState([]);
  const [steps, setSteps] = useState([]);
  const [busy, setBusy] = useState(false);
  const [asked, setAsked] = useState("");
  const [attachments, setAttachments] = useState({});
  const inputRef = useRef(null);

  function toggle(key) {
    setAttachments((a) => ({ ...a, [key]: !a[key] }));
    inputRef.current?.focus();
  }

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

  function run(text, ctx) {
    const context = ctx || attachments;
    // With the screen attached you can send an empty box ("what's this?").
    if ((!text && !context.screen) || busy) return;
    setAsked(text);
    setReply("");
    setCards([]);
    setSteps([]);
    setBusy(true);
    sendCommand(text, (chunk) => {
      setReply(chunk.reply || "");
      if (Array.isArray(chunk.steps)) setSteps(chunk.steps);
      if (chunk.done) {
        if (Array.isArray(chunk.cards)) setCards(chunk.cards);
        setBusy(false);
      }
    }, context);
  }

  function submit(e) {
    e.preventDefault();
    run(q.trim());
  }

  function retry() {
    if (asked || attachments.screen) run(asked);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (reply || busy) {
        setReply("");
        setCards([]);
        setSteps([]);
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
          placeholder={
            !connected ? "Connecting…" : attachments.screen ? "Ask about your screen…" : "Ask Jarvis…"
          }
          spellCheck={false}
          autoComplete="off"
        />
        <kbd className="cmd-kbd">{busy ? "…" : "⏎"}</kbd>
      </form>
      <ContextBar attachments={attachments} onToggle={toggle} />
      {(reply || busy) && (
        <div className="cmd-result">
          <AgentTrace steps={steps} />
          {reply ? <Markdown>{reply}</Markdown> : !steps.length && "Thinking…"}
          {busy && reply && <span className="cmd-caret" />}
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
