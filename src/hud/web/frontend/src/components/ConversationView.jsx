import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";
import Markdown from "./Markdown.jsx";
import CopyButton from "./CopyButton.jsx";
import { ResultCards } from "./ResultCard.jsx";

const STATE_LABEL = {
  idle: "IDLE",
  wake_detected: "WAKE DETECTED",
  listening: "LISTENING",
  transcribing: "TRANSCRIBING",
  thinking: "THINKING",
  awaiting_approval: "NEEDS YOUR OK",
  executing: "WORKING",
  speaking: "SPEAKING",
  interrupted: "INTERRUPTED",
  initializing: "STARTING",
};

const ACTIVE = new Set(["listening", "transcribing", "thinking", "executing", "speaking"]);

const WAVE_BARS = 18;

export default function ConversationView() {
  const { status, request, send } = useJarvis();
  const [turns, setTurns] = useState([]);
  const [decided, setDecided] = useState(null);
  const [levels, setLevels] = useState(() => Array(WAVE_BARS).fill(0));
  const [cards, setCards] = useState([]);
  const state = status.state || "idle";

  // Keep the current turn's result cards visible until the next turn begins.
  useEffect(() => {
    if (Array.isArray(status.cards) && status.cards.length) setCards(status.cards);
  }, [status.cards]);
  useEffect(() => {
    if (state === "listening" || state === "wake_detected") setCards([]);
  }, [state]);

  // Feed the live mic level into a scrolling waveform buffer while listening.
  useEffect(() => {
    if (state === "listening" && typeof status.level === "number") {
      setLevels((prev) => [...prev.slice(1), status.level]);
    } else if (state !== "listening") {
      setLevels((prev) => (prev.some((v) => v > 0) ? Array(WAVE_BARS).fill(0) : prev));
    }
  }, [status.level, state]);

  function decide(approved) {
    if (status.approval_id) {
      send({ type: "approval_decision", id: status.approval_id, approved });
      setDecided(approved ? "Approved" : "Denied");
    }
  }

  // Refresh recent history whenever a turn finishes (idle) or a reply lands.
  useEffect(() => {
    if (state === "idle" || state === "speaking") {
      request("list_transcript", { limit: 8 }).then((r) => setTurns(r.turns || []));
    }
  }, [state, status.reply, request]);

  // Clear the local decision label when a new approval appears.
  useEffect(() => {
    setDecided(null);
  }, [status.approval_id]);

  const liveHeard = status.transcript || "";
  const liveReply = status.reply || "";
  const showLive = ACTIVE.has(state) && (liveHeard || liveReply);

  return (
    <div className="conv">
      <div className="stage">
        <div className={"orb tone-" + (ACTIVE.has(state) ? "on" : "idle")}>
          <div className="orb-ring" />
          <div className="orb-core" />
        </div>
        <div className="stage-txt">
          <div className="stage-label">● {STATE_LABEL[state] || state.toUpperCase()}</div>
          <div className="wave" aria-hidden="true">
            {state === "listening"
              ? levels.map((lv, i) => (
                  <i key={i} className="live" style={{ height: `${3 + Math.min(1, lv) * 23}px` }} />
                ))
              : Array.from({ length: WAVE_BARS }).map((_, i) => (
                  <i
                    key={i}
                    className={ACTIVE.has(state) ? "on" : ""}
                    style={{ animationDelay: `${(i % 7) * 0.09}s` }}
                  />
                ))}
          </div>
          {liveHeard ? (
            <div className="heard"><span className="cue">heard:</span> “{liveHeard}”</div>
          ) : (
            <div className="heard cue">Say “Hey Jarvis”, then speak.</div>
          )}
        </div>
      </div>

      {status.state === "awaiting_approval" && (
        <div className="approve">
          <div className="approve-top">
            <span className="badge">NEEDS YOU</span>
            <span className="approve-sub">Jarvis wants to do something that changes things</span>
          </div>
          <div className="approve-mid">
            <h4>{status.description || "Confirm this action"}</h4>
            <div className="kv">
              {status.tool && <span>tool <b>{status.tool}</b></span>}
              <span>approve <b>say “confirm”</b></span>
            </div>
          </div>
          <div className="approve-act">
            {decided ? (
              <span className="hintline">{decided} — waiting for Jarvis…</span>
            ) : (
              <>
                <button className="btn deny" onClick={() => decide(false)}>Deny</button>
                <button className="btn go" onClick={() => decide(true)}>Approve</button>
                <span className="hintline">or say “confirm” aloud</span>
              </>
            )}
          </div>
        </div>
      )}

      <div className="thread">
        {turns.map((t, i) => (
          <div key={i} className="pair">
            <div className="msg you">{t.transcript}</div>
            <div className="msg jarvis">
              <Markdown>{t.reply}</Markdown>
              <div className="msg-act"><CopyButton text={t.reply} /></div>
            </div>
          </div>
        ))}
        {showLive && (
          <div className="pair live">
            {liveHeard && <div className="msg you">{liveHeard}</div>}
            {liveReply && (
              <div className="msg jarvis">
                <Markdown>{liveReply}</Markdown>
                {state === "speaking" && <span className="caret" />}
                {state !== "speaking" && <div className="msg-act"><CopyButton text={liveReply} /></div>}
              </div>
            )}
            <ResultCards cards={cards} />
          </div>
        )}
        {!showLive && cards.length > 0 && (
          <div className="pair"><ResultCards cards={cards} /></div>
        )}
        {turns.length === 0 && !showLive && (
          <div className="empty">No conversation yet. Say “Hey Jarvis” to begin.</div>
        )}
      </div>
    </div>
  );
}
