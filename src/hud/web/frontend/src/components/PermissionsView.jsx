import { useEffect, useState } from "react";
import { useJarvis } from "../jarvisClient.jsx";

export default function PermissionsView() {
  const { request } = useJarvis();
  const [folders, setFolders] = useState([]);

  useEffect(() => {
    request("get_config").then((r) => setFolders(r?.config?.filesystem_allowlist || []));
  }, [request]);

  const perms = [
    { name: "Microphone", why: "Hear the wake word and your commands.", state: "Granted", tone: "ok" },
    { name: "Speaker", why: "Speak replies aloud.", state: "Granted", tone: "ok" },
    { name: "Files & folders", why: "Read/write only inside the folders below.", state: `${folders.length} allowed`, tone: "ok" },
    { name: "Network", why: "Local models are offline; web search uses the internet.", state: "Web search only", tone: "ok" },
  ];

  return (
    <div className="view">
      <div className="view-head">
        <h2>Permissions</h2>
        <p className="sub">Everything Jarvis is allowed to touch, and why. Reasoning runs fully on-device.</p>
      </div>
      <div className="perm-list">
        {perms.map((p) => (
          <div key={p.name} className="perm">
            <div className="perm-main">
              <span className="perm-name">{p.name}</span>
              <span className="perm-why">{p.why}</span>
            </div>
            <span className={"chip chip-" + p.tone}>{p.state}</span>
          </div>
        ))}
      </div>
      <div className="view-head" style={{ marginTop: "18px" }}>
        <h3>Allowed folders</h3>
      </div>
      <div className="folder-list">
        {folders.length === 0 && <div className="empty">None configured.</div>}
        {folders.map((f) => (
          <code key={f} className="folder">{f}</code>
        ))}
      </div>
    </div>
  );
}
