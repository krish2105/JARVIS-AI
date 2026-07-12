import { useState } from "react";
import { useJarvis } from "./jarvisClient.jsx";
import ConversationView from "./components/ConversationView.jsx";
import HistoryView from "./components/HistoryView.jsx";
import MemoryView from "./components/MemoryView.jsx";
import SkillsView from "./components/SkillsView.jsx";
import PermissionsView from "./components/PermissionsView.jsx";
import ActivityView from "./components/ActivityView.jsx";
import SettingsView from "./components/SettingsView.jsx";
import Onboarding from "./components/Onboarding.jsx";

const TABS = [
  { id: "conversation", label: "Conversation", icon: "◉" },
  { id: "history", label: "History", icon: "≣" },
  { id: "memory", label: "Memory", icon: "◈" },
  { id: "skills", label: "Skills", icon: "✦", tag: "NEW" },
  { id: "permissions", label: "Permissions", icon: "⛨", tag: "NEW" },
  { id: "activity", label: "Activity", icon: "⌁" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

const STATE_META = {
  idle: { label: "IDLE", tone: "idle" },
  wake_detected: { label: "WAKE", tone: "listen" },
  listening: { label: "LISTENING", tone: "listen" },
  transcribing: { label: "TRANSCRIBING", tone: "think" },
  thinking: { label: "THINKING", tone: "think" },
  awaiting_approval: { label: "NEEDS YOU", tone: "approve" },
  executing: { label: "WORKING", tone: "think" },
  speaking: { label: "SPEAKING", tone: "speak" },
  interrupted: { label: "INTERRUPTED", tone: "approve" },
  initializing: { label: "STARTING", tone: "idle" },
  error: { label: "ERROR", tone: "approve" },
  degraded: { label: "DEGRADED", tone: "approve" },
};

export default function App() {
  const [tab, setTab] = useState("conversation");
  const { connected, status } = useJarvis();
  const meta = STATE_META[status.state] || STATE_META.idle;

  return (
    <div className="app">
      <Onboarding />
      <header className="titlebar">
        <span className="wordmark">JAR<b>V</b>IS</span>
        <span className={"statepill tone-" + meta.tone}>
          <span className="dot" />
          {meta.label}
        </span>
      </header>

      <div className="body">
        <nav className="sidebar">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={"nav-item" + (tab === t.id ? " active" : "")}
              onClick={() => setTab(t.id)}
            >
              <span className="nav-icon">{t.icon}</span>
              <span className="nav-label">{t.label}</span>
              {t.tag && <span className="nav-tag">{t.tag}</span>}
            </button>
          ))}
          <div className="sidebar-footer">
            <span className={"conn " + (connected ? "ok" : "off")}>
              ● {connected ? "connected" : "no server"}
            </span>
          </div>
        </nav>

        <main className="content">
          {tab === "conversation" && <ConversationView />}
          {tab === "history" && <HistoryView />}
          {tab === "memory" && <MemoryView />}
          {tab === "skills" && <SkillsView />}
          {tab === "permissions" && <PermissionsView />}
          {tab === "activity" && <ActivityView />}
          {tab === "settings" && <SettingsView />}
        </main>
      </div>
    </div>
  );
}
