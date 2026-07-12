import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

const VOICES = [
  { id: "am_liam", label: "Liam (US, warm)" },
  { id: "am_adam", label: "Adam (US, deep)" },
  { id: "af_heart", label: "Heart (US, bright)" },
  { id: "af_bella", label: "Bella (US, soft)" },
  { id: "bm_george", label: "George (UK)" },
];

export default function Onboarding() {
  const { request } = useJarvis();
  const [show, setShow] = useState(false);
  const [step, setStep] = useState(0);
  const [voice, setVoice] = useState("am_liam");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    request("get_config").then((r) => {
      const cfg = r?.config || {};
      if (cfg.onboarded === false || cfg.onboarded === undefined) setShow(true);
      if (cfg.voice) setVoice(cfg.voice);
    });
  }, [request]);

  if (!show) return null;

  async function finish() {
    setSaving(true);
    await request("save_config", { patch: { voice, onboarded: true } });
    setShow(false);
  }

  const steps = [
    {
      title: "Welcome to Jarvis",
      body: (
        <>
          <p>Your assistant runs <b>entirely on this Mac</b> — speech, reasoning, and voice are all local. Nothing leaves your machine except an explicit web search.</p>
          <p>Let's get you set up in a few steps.</p>
        </>
      ),
    },
    {
      title: "Talking to Jarvis",
      body: (
        <>
          <p>Just say <b>“Hey Jarvis”</b>, wait for the ring to light up, then speak. You can interrupt it any time by saying “Hey Jarvis” again.</p>
          <p className="muted-note">If it doesn't hear you, allow the microphone in System Settings › Privacy &amp; Security › Microphone.</p>
        </>
      ),
    },
    {
      title: "Pick a voice",
      body: (
        <>
          <p>Choose how Jarvis sounds. You can change this later in Settings.</p>
          <select value={voice} onChange={(e) => setVoice(e.target.value)}>
            {VOICES.map((v) => (
              <option key={v.id} value={v.id}>{v.label}</option>
            ))}
          </select>
        </>
      ),
    },
    {
      title: "You're all set",
      body: (
        <>
          <p>Try these to start:</p>
          <ul className="ob-list">
            <li>“Hey Jarvis, what's the weather in London?”</li>
            <li>“Hey Jarvis, set a 10 minute timer.”</li>
            <li>“Hey Jarvis, what's on my calendar today?”</li>
          </ul>
        </>
      ),
    },
  ];

  const s = steps[step];
  const last = step === steps.length - 1;

  return (
    <div className="ob-overlay">
      <div className="ob-card">
        <div className="ob-dots">
          {steps.map((_, i) => (
            <span key={i} className={"ob-dot" + (i === step ? " on" : "")} />
          ))}
        </div>
        <h2>{s.title}</h2>
        <div className="ob-body">{s.body}</div>
        <div className="ob-actions">
          {step > 0 && <button className="btn" onClick={() => setStep(step - 1)}>Back</button>}
          <span style={{ flex: 1 }} />
          {!last ? (
            <button className="btn go" onClick={() => setStep(step + 1)}>Next</button>
          ) : (
            <button className="btn go" onClick={finish} disabled={saving}>
              {saving ? "Saving…" : "Start using Jarvis"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
