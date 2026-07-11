import { useState } from "react";
import { useJarvis } from "./jarvisClient.jsx";
import StatusView from "./components/StatusView.jsx";
import HistoryView from "./components/HistoryView.jsx";
import MemoryView from "./components/MemoryView.jsx";
import SettingsView from "./components/SettingsView.jsx";
import ActivityView from "./components/ActivityView.jsx";

const TABS = [
  { id: "status", label: "Status", icon: "◎" },
  { id: "history", label: "History", icon: "☷" },
  { id: "memory", label: "Memory", icon: "▦" },
  { id: "activity", label: "Activity", icon: "⚙" },
  { id: "settings", label: "Settings", icon: "⚙︎" },
];

export default function App() {
  const [tab, setTab] = useState("status");
  const { connected } = useJarvis();

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">Jarvis</div>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={"nav-item" + (tab === t.id ? " active" : "")}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <div className="sidebar-footer">
          <span className={"dot " + (connected ? "dot-connected" : "dot-disconnected")} />
          {connected ? "connected" : "no HUD server"}
        </div>
      </nav>
      <main className="content">
        {tab === "status" && <StatusView />}
        {tab === "history" && <HistoryView />}
        {tab === "memory" && <MemoryView />}
        {tab === "activity" && <ActivityView />}
        {tab === "settings" && <SettingsView />}
      </main>
    </div>
  );
}
