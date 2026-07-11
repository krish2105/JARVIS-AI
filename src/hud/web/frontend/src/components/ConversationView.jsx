import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

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

export default function ConversationView() {
  const { status, request } = useJarvis();
  const [turns, setTurns] = useState([]);
  const state = status.state || "idle";

  // Refresh recent history whenever a turn finishes (idle) or a reply lands.
  useEffect(() => {
    if (state === "idle" || state === "speaking") {
      request("list_transcript", { limit: 8 }).then((r) => setTurns(r.turns || []));
    }
  }, [state, status.reply, request]);

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
            {Array.from({ length: 13 }).map((_, i) => (
              <i key={i} className={ACTIVE.has(state) ? "on" : ""} style={{ animationDelay: `${(i % 7) * 0.09}s` }} />
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
            <span className="hintline">Speak “confirm” to approve, or say anything else to cancel.</span>
          </div>
        </div>
      )}

      <div className="thread">
        {turns.map((t, i) => (
          <div key={i} className="pair">
            <div className="msg you">{t.transcript}</div>
            <div className="msg jarvis">{t.reply}</div>
          </div>
        ))}
        {showLive && (
          <div className="pair live">
            {liveHeard && <div className="msg you">{liveHeard}</div>}
            {liveReply && (
              <div className="msg jarvis">
                {liveReply}
                {state === "speaking" && <span className="caret" />}
              </div>
            )}
          </div>
        )}
        {turns.length === 0 && !showLive && (
          <div className="empty">No conversation yet. Say “Hey Jarvis” to begin.</div>
        )}
      </div>
    </div>
  );
}
