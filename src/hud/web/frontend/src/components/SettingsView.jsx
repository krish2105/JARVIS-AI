import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

const CONFIRMATION_OPTIONS = ["delete_file", "run_shell_command", "Write"];

function flatten(config) {
  return {
    wake_word: config.wake_word,
    voice: config.voice,
    "model.local": config.model.local,
    "model.local_heavy": config.model.local_heavy,
    "audio.wake_word_sensitivity": config.audio.wake_word_sensitivity,
    "audio.vad_silence_ms": config.audio.vad_silence_ms,
    "audio.max_record_seconds": config.audio.max_record_seconds,
    filesystem_allowlist_text: (config.filesystem_allowlist || []).join("\n"),
    require_confirmation_for: config.require_confirmation_for || [],
    "rag.folders_text": ((config.rag && config.rag.folders) || []).join("\n"),
    "rag.include_notes": !!(config.rag && config.rag.include_notes),
    "rag.use_embeddings": !!(config.rag && config.rag.use_embeddings),
    "rag.max_files": (config.rag && config.rag.max_files) || 800,
  };
}

export default function SettingsView() {
  const { request } = useJarvis();
  const [form, setForm] = useState(null);
  const [status, setStatus] = useState("");
  const [ragStatus, setRagStatus] = useState("");
  const [reindexing, setReindexing] = useState(false);

  useEffect(() => {
    request("get_config").then((result) => {
      if (result.config) setForm(flatten(result.config));
    });
    request("rag_status").then((r) => {
      if (typeof r.chunks === "number") setRagStatus(`${r.chunks} chunks indexed`);
    });
  }, []);

  async function reindex() {
    setReindexing(true);
    setRagStatus("Reindexing… (first run downloads the embedding model)");
    // Persist folder/notes/embedding changes first so the reindex uses them.
    await save({ silent: true });
    const r = await request("reindex_documents");
    setReindexing(false);
    setRagStatus(r.error ? "Error: " + r.error : r.message || "Done.");
  }

  if (!form) {
    return (
      <div className="view">
        <h2>Settings</h2>
        <p>Loading…</p>
      </div>
    );
  }

  function update(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleConfirmation(name) {
    const current = new Set(form.require_confirmation_for || []);
    if (current.has(name)) current.delete(name);
    else current.add(name);
    update("require_confirmation_for", Array.from(current));
  }

  async function save(opts = {}) {
    if (!opts.silent) setStatus("Saving…");
    const patch = {
      wake_word: form.wake_word,
      voice: form.voice,
      "model.local": form["model.local"],
      "model.local_heavy": form["model.local_heavy"],
      "audio.wake_word_sensitivity": Number(form["audio.wake_word_sensitivity"]),
      "audio.vad_silence_ms": Number(form["audio.vad_silence_ms"]),
      "audio.max_record_seconds": Number(form["audio.max_record_seconds"]),
      filesystem_allowlist: (form.filesystem_allowlist_text || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      require_confirmation_for: form.require_confirmation_for || [],
      "rag.folders": (form["rag.folders_text"] || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      "rag.include_notes": !!form["rag.include_notes"],
      "rag.use_embeddings": !!form["rag.use_embeddings"],
      "rag.max_files": Number(form["rag.max_files"]),
    };
    const result = await request("save_config", { patch });
    if (result.error) {
      setStatus("Error: " + result.error);
      return false;
    }
    if (!opts.silent) setStatus("Saved. Folder/notes changes apply on the next search; model changes need a restart.");
    return true;
  }

  return (
    <div className="view settings-view">
      <h2>Settings</h2>

      <label>
        Wake word
        <input value={form.wake_word} onChange={(e) => update("wake_word", e.target.value)} />
      </label>

      <label>
        TTS voice
        <input value={form.voice} onChange={(e) => update("voice", e.target.value)} />
      </label>

      <label>
        Local model (everyday)
        <input value={form["model.local"]} onChange={(e) => update("model.local", e.target.value)} />
      </label>

      <label>
        Local model (heavy / planning)
        <input
          value={form["model.local_heavy"]}
          onChange={(e) => update("model.local_heavy", e.target.value)}
        />
      </label>

      <label>
        Wake word sensitivity ({form["audio.wake_word_sensitivity"]})
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={form["audio.wake_word_sensitivity"]}
          onChange={(e) => update("audio.wake_word_sensitivity", e.target.value)}
        />
      </label>

      <label>
        Silence before end-of-turn (ms)
        <input
          type="number"
          value={form["audio.vad_silence_ms"]}
          onChange={(e) => update("audio.vad_silence_ms", e.target.value)}
        />
      </label>

      <label>
        Max recording length (seconds)
        <input
          type="number"
          value={form["audio.max_record_seconds"]}
          onChange={(e) => update("audio.max_record_seconds", e.target.value)}
        />
      </label>

      <label>
        Filesystem allowlist (one path per line)
        <textarea
          rows={4}
          value={form.filesystem_allowlist_text}
          onChange={(e) => update("filesystem_allowlist_text", e.target.value)}
        />
      </label>

      <fieldset>
        <legend>Files &amp; notes (RAG)</legend>
        <label>
          Folders to index (one path per line)
          <textarea
            rows={3}
            value={form["rag.folders_text"]}
            placeholder="~/Documents"
            onChange={(e) => update("rag.folders_text", e.target.value)}
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={form["rag.include_notes"]}
            onChange={(e) => update("rag.include_notes", e.target.checked)}
          />
          Include Apple Notes
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={form["rag.use_embeddings"]}
            onChange={(e) => update("rag.use_embeddings", e.target.checked)}
          />
          Semantic search (local embeddings) — off = keyword only
        </label>
        <label>
          Max files to index
          <input
            type="number"
            value={form["rag.max_files"]}
            onChange={(e) => update("rag.max_files", e.target.value)}
          />
        </label>
        <div className="settings-actions">
          <button onClick={reindex} disabled={reindexing}>
            {reindexing ? "Reindexing…" : "Save & reindex now"}
          </button>
          <span className="settings-status">{ragStatus}</span>
        </div>
      </fieldset>

      <fieldset>
        <legend>Require confirmation for</legend>
        {CONFIRMATION_OPTIONS.map((name) => (
          <label key={name} className="checkbox-label">
            <input
              type="checkbox"
              checked={(form.require_confirmation_for || []).includes(name)}
              onChange={() => toggleConfirmation(name)}
            />
            {name}
          </label>
        ))}
      </fieldset>

      <div className="settings-actions">
        <button onClick={() => save()}>Save</button>
        <span className="settings-status">{status}</span>
      </div>
    </div>
  );
}
