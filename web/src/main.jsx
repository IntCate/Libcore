import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import { registerBuiltins } from "./components/index.js";
import { reportManifest, watchManifest } from "./services/manifest.js";
import { connectEvents } from "./services/events.js";
import "./styles.css";

registerBuiltins();
watchManifest();
reportManifest();
connectEvents(); // 接入后端 WebSocket 下行 -> consumeRender

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);