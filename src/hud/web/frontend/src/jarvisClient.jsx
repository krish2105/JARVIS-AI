import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

const WS_URL = "ws://127.0.0.1:8765";
const RECONNECT_MS = 1500;

const JarvisContext = createContext(null);

export function JarvisProvider({ children }) {
  const wsRef = useRef(null);
  const pending = useRef(new Map());
  const [status, setStatus] = useState({ state: "idle", transcript: "", reply: "" });
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let socket;
    let reconnectTimer;

    function connect() {
      socket = new WebSocket(WS_URL);
      wsRef.current = socket;

      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };
      socket.onerror = () => socket.close();
      socket.onmessage = (event) => {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        if (payload.type === "response") {
          const resolve = pending.current.get(payload.id);
          if (resolve) {
            resolve(payload.result);
            pending.current.delete(payload.id);
          }
          return;
        }
        setStatus(payload);
      };
    }

    connect();
    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      socket && socket.close();
    };
  }, []);

  const request = useCallback((action, params = {}) => {
    return new Promise((resolve) => {
      const socket = wsRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        resolve({ error: "not connected to Jarvis" });
        return;
      }
      const id = Math.random().toString(36).slice(2);
      pending.current.set(id, resolve);
      socket.send(JSON.stringify({ type: "request", id, action, params }));
    });
  }, []);

  // Fire-and-forget message (e.g. an approval decision relayed to the pipeline).
  const send = useCallback((obj) => {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(obj));
    }
  }, []);

  return (
    <JarvisContext.Provider value={{ status, connected, request, send }}>
      {children}
    </JarvisContext.Provider>
  );
}

export function useJarvis() {
  const ctx = useContext(JarvisContext);
  if (!ctx) throw new Error("useJarvis must be used inside a JarvisProvider");
  return ctx;
}
