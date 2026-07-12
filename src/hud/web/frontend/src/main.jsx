import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import OrbWidget from "./components/OrbWidget.jsx";
import { JarvisProvider } from "./jarvisClient.jsx";
import "./styles.css";

// The same bundle serves the full HUD and the ambient desktop orb; the orb
// window loads index.html#orb (see src/main.py). In orb mode the page is
// transparent so only the orb floats.
const isOrb = window.location.hash === "#orb";
if (isOrb) document.body.classList.add("orb-mode");

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <JarvisProvider>{isOrb ? <OrbWidget /> : <App />}</JarvisProvider>
  </React.StrictMode>
);
