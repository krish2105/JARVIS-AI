import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import OrbWidget from "./components/OrbWidget.jsx";
import CommandBar from "./components/CommandBar.jsx";
import { JarvisProvider } from "./jarvisClient.jsx";
import "./styles.css";

// The same bundle serves three windows (see src/main.py): the full HUD, the
// ambient desktop orb (#orb), and the Spotlight-style command bar (#command).
// The orb and command bar render on a transparent page so they float.
const hash = window.location.hash;
const isOrb = hash === "#orb";
const isCommand = hash === "#command";
if (isOrb || isCommand) document.body.classList.add("orb-mode");

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <JarvisProvider>
      {isOrb ? <OrbWidget /> : isCommand ? <CommandBar /> : <App />}
    </JarvisProvider>
  </React.StrictMode>
);
