import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";
import { formatTime } from "../format.js";

export default function HistoryView() {
  const { request, status } = useJarvis();
  const [turns, setTurns] = useState([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const result = await request("list_transcript", { limit: 100 });
    setTurns(result.turns || []);
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, []);

  // Live-append the just-completed turn so it shows immediately instead of
  // waiting for a manual refresh; dedup against what a refresh already had.
  useEffect(() => {
    if (status.state === "speaking" && status.transcript && status.reply) {
      setTurns((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.transcript === status.transcript && last.reply === status.reply) {
          return prev;
        }
        return [...prev, { transcript: status.transcript, reply: status.reply, timestamp: new Date().toISOString() }];
      });
    }
  }, [status]);

  return (
    <div className="view">
      <div className="view-header">
        <h2>Conversation history</h2>
        <button onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      {turns.length === 0 && !loading && <p className="empty">No conversations yet.</p>}
      <ul className="turn-list">
        {turns
          .slice()
          .reverse()
          .map((t, i) => (
            <li key={i} className="turn">
              <div className="turn-time">{formatTime(t.timestamp)}</div>
              <div className="turn-you">You: {t.transcript}</div>
              <div className="turn-jarvis">Jarvis: {t.reply}</div>
            </li>
          ))}
      </ul>
    </div>
  );
}
