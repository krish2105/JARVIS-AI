const SKILLS = [
  { name: "Timers", desc: "Set countdown timers — Jarvis announces them aloud when they finish.", risk: "safe" },
  { name: "Weather", desc: "Current conditions and today's high/low for any place.", risk: "safe" },
  { name: "Reminders", desc: "Add items to the macOS Reminders app.", risk: "safe" },
  { name: "Calendar", desc: "Read your events and add new ones to the macOS Calendar.", risk: "safe" },
  { name: "Memory", desc: "Remember and recall facts about you across restarts.", risk: "safe" },
  { name: "Web search", desc: "Search the web and summarize the top results.", risk: "safe" },
  { name: "Read files", desc: "Read text files inside your allowed folders.", risk: "safe" },
  { name: "Write files", desc: "Create or overwrite a file — asks you to confirm first.", risk: "confirm" },
  { name: "Run command", desc: "Run one allowed program (ls, git, grep…) — confirmed, no shell.", risk: "confirm" },
  { name: "Browser control", desc: "Navigate and act on web pages — mutations confirmed.", risk: "confirm" },
];

const RISK_LABEL = { safe: "no confirm", confirm: "asks first" };

export default function SkillsView() {
  return (
    <div className="view">
      <div className="view-head">
        <h2>Skills</h2>
        <p className="sub">What Jarvis can do. Anything that changes files, runs a command, or acts on the web asks you to say “confirm” first.</p>
      </div>
      <div className="cards">
        {SKILLS.map((s) => (
          <div key={s.name} className="card">
            <div className="card-top">
              <span className="card-title">{s.name}</span>
              <span className={"chip chip-" + s.risk}>{RISK_LABEL[s.risk]}</span>
            </div>
            <p className="card-desc">{s.desc}</p>
          </div>
        ))}
      </div>
      <p className="foot-note">Coming next: Music and smart-home control.</p>
    </div>
  );
}
