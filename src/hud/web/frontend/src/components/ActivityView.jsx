import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";
import { formatTime } from "../format.js";

export default function ActivityView() {
  const { request } = useJarvis();
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const result = await request("list_tool_calls", { limit: 100 });
    setCalls(result.calls || []);
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="view">
      <div className="view-header">
        <h2>Tool activity</h2>
        <button onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <p className="hint">
        Every tool call Jarvis actually executes shows up here — if it claimed to do something
        and it's not in this list, it didn't happen.
      </p>
      {calls.length === 0 && !loading && <p className="empty">No tool calls yet.</p>}
      <ul className="call-list">
        {calls
          .slice()
          .reverse()
          .map((c, i) => (
            <li key={i} className="call">
              <div className="call-time">{formatTime(c.timestamp)}</div>
              <div className="call-tool">{c.tool}</div>
              <pre className="call-input">{JSON.stringify(c.input, null, 2)}</pre>
              <div className="call-result">{c.result}</div>
            </li>
          ))}
      </ul>
    </div>
  );
}
