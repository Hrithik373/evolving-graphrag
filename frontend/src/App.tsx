import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { api } from "./lib/api";
import { usePolling, useTheme } from "./lib/hooks";
import Dashboard from "./pages/Dashboard";
import Documents from "./pages/Documents";
import Query from "./pages/Query";
import GraphExplorer from "./pages/GraphExplorer";
import Communities from "./pages/Communities";
import Maintenance from "./pages/Maintenance";
import Resolution from "./pages/Resolution";

const NAV = [
  { to: "/dashboard", label: "Dashboard", glyph: "◧" },
  { to: "/documents", label: "Documents", glyph: "▤" },
  { to: "/query", label: "Query", glyph: "◈" },
  { to: "/graph", label: "Graph", glyph: "⁘" },
  { to: "/communities", label: "Communities", glyph: "◍" },
  { to: "/resolution", label: "Resolution", glyph: "⇄" },
  { to: "/maintenance", label: "Maintenance", glyph: "⟳" },
];

export default function App() {
  const [theme, setTheme] = useTheme();
  const { data: ready } = usePolling(api.ready, 10_000);
  const { data: config } = usePolling(api.config, 0);

  return (
    <div className="app">
      <aside className="sidebar">
        <div>
          <div className="brand">
            <span aria-hidden style={{ color: "var(--series-1)" }}>◆</span>
            <span>
              evolving-graphrag
              <small>incremental index maintenance</small>
            </span>
          </div>
        </div>

        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <span aria-hidden style={{ width: 14, display: "inline-block" }}>{item.glyph}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="spacer" />

        <div className="stack small">
          <div>
            {ready ? (
              <span className={`badge ${ready.ready ? "good" : "bad"}`}>
                {ready.ready ? "● connected" : "▲ degraded"}
              </span>
            ) : (
              <span className="badge muted">○ connecting</span>
            )}
          </div>
          {ready && (
            <div className="muted mono" style={{ lineHeight: 1.7 }}>
              store {ready.store_backend}
              <br />
              queue {ready.queue_backend}
              <br />
              llm {ready.llm_backend}
              {config && (
                <>
                  <br />
                  cfg {config.config_hash.slice(0, 8)}
                </>
              )}
            </div>
          )}
          <div className="row" style={{ gap: 4 }}>
            {(["light", "dark", "system"] as const).map((option) => (
              <button
                key={option}
                className={theme === option ? "" : "ghost"}
                style={{ padding: "3px 8px", fontSize: 11 }}
                onClick={() => setTheme(option)}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/query" element={<Query />} />
          <Route path="/graph" element={<GraphExplorer />} />
          <Route path="/communities" element={<Communities />} />
          <Route path="/resolution" element={<Resolution />} />
          <Route path="/maintenance" element={<Maintenance />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}
