// Live agent-trace: a compact list of the tool steps Jarvis takes during a
// turn, streamed in as they run. Each row shows the tool, its key argument,
// and a status glyph (spinner → ✓ / denied / error). Hover for the result.
const TOOL_ICON = {
  get_weather: "☁",
  list_events: "▦",
  create_event: "▦",
  create_reminder: "✓",
  list_reminders: "▤",
  music_current: "♪",
  music_play: "♪",
  music_control: "♪",
  look_at_screen: "▢",
  check_email: "✉",
  search_email: "✉",
  send_email: "✉",
  read_messages: "💬",
  send_message: "💬",
  search_documents: "▤",
  reindex_documents: "⟳",
  memory: "🧠",
  web_search: "🔎",
  run_shell: "›_",
  read_file: "▤",
  set_timer: "⏱",
  set_volume: "◍",
  open_app: "▦",
};

const STATUS_GLYPH = { running: "", done: "✓", denied: "⃠", error: "!" };

function summarizeInput(input) {
  if (!input || typeof input !== "object") return "";
  return Object.values(input)
    .filter((v) => typeof v === "string" || typeof v === "number")
    .map((v) => String(v))
    .join(", ")
    .slice(0, 60);
}

export default function AgentTrace({ steps }) {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  return (
    <div className="trace">
      {steps.map((s, i) => {
        const arg = summarizeInput(s.input);
        return (
          <div key={i} className={"trace-step st-" + s.status} title={s.result || ""}>
            <span className="ts-ico">{TOOL_ICON[s.tool] || "◆"}</span>
            <span className="ts-name">{s.tool}</span>
            {arg && <span className="ts-arg">{arg}</span>}
            {s.status === "running" ? (
              <span className="ts-spin" aria-label="running" />
            ) : (
              <span className="ts-status">{STATUS_GLYPH[s.status] || "•"}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
