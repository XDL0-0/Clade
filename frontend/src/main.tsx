import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { QueryProvider } from "@/providers/QueryProvider";

import "./styles/tokens.css"; // 设计系统变量
import "./styles.css";
import "./animations.css";
import "./enhancements.css"; // 界面增强样式
import "./play-entry.css";

const EvolutionLab = React.lazy(() => import("./v2/EvolutionLab"));
const showLab = /^\/(lab|play)\/?$/.test(window.location.pathname);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {showLab ? (
      <React.Suspense fallback={<p role="status">正在打开生命演化沙盒…</p>}>
        <EvolutionLab />
      </React.Suspense>
    ) : (
      <QueryProvider>
        <App />
        <a className="clade-play-entry" href="/play">生命演化沙盒 ↗</a>
      </QueryProvider>
    )}
  </React.StrictMode>
);
