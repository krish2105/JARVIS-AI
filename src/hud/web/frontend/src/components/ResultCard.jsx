// Glanceable structured result cards — weather, agenda, now-playing, and
// "look at my screen" answers get their own styled card instead of plain text.
const META = {
  weather: { icon: "☁", accent: "wx" },
  agenda: { icon: "▦", accent: "ag" },
  music: { icon: "♪", accent: "mu" },
  screen: { icon: "▢", accent: "sc" },
  email: { icon: "✉", accent: "em" },
  messages: { icon: "💬", accent: "ms" },
  docs: { icon: "▤", accent: "dc" },
};

export default function ResultCard({ card }) {
  if (!card) return null;
  const m = META[card.type] || { icon: "•", accent: "gen" };
  return (
    <div className={"rcard rc-" + m.accent}>
      <div className="rc-head">
        <span className="rc-ico">{m.icon}</span>
        <span className="rc-title">{card.title || card.type}</span>
      </div>
      <div className="rc-body">{card.text}</div>
    </div>
  );
}

export function ResultCards({ cards }) {
  if (!Array.isArray(cards) || cards.length === 0) return null;
  return (
    <div className="rcards">
      {cards.map((c, i) => (
        <ResultCard key={i} card={c} />
      ))}
    </div>
  );
}
