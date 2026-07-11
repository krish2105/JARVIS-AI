import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

export default function MemoryView() {
  const { request } = useJarvis();
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);

  async function loadFiles() {
    setLoading(true);
    const result = await request("list_memories");
    setFiles(result.files || []);
    setLoading(false);
  }

  useEffect(() => {
    loadFiles();
  }, []);

  async function openFile(name) {
    setSelected(name);
    const result = await request("read_memory", { name });
    setContent(result.content ?? result.error ?? "");
  }

  async function deleteFile(name) {
    if (!window.confirm(`Delete memory file "${name}"? This can't be undone.`)) return;
    await request("delete_memory", { name });
    if (selected === name) {
      setSelected(null);
      setContent("");
    }
    loadFiles();
  }

  return (
    <div className="view memory-view">
      <div className="view-header">
        <h2>Memory</h2>
        <button onClick={loadFiles} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <div className="memory-layout">
        <ul className="memory-file-list">
          {files.length === 0 && <li className="empty">No memory files yet.</li>}
          {files.map((f) => (
            <li key={f.name} className={selected === f.name ? "active" : ""}>
              <button className="memory-file-button" onClick={() => openFile(f.name)}>
                {f.name} <span className="file-size">{f.size}B</span>
              </button>
              <button className="delete-button" onClick={() => deleteFile(f.name)} title="Delete">
                ✕
              </button>
            </li>
          ))}
        </ul>
        <pre className="memory-content">{selected ? content : "Select a file to view its contents."}</pre>
      </div>
    </div>
  );
}
