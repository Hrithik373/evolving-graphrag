/**
 * Ask the index a question. Every answer carries its provenance and says how many stale
 * community summaries it leaned on, so freshness is visible rather than assumed.
 */

import { useState } from "react";

import { api, type Answer } from "../lib/api";

const MODES = [
  { id: "dual", label: "dual", hint: "entities + community summaries" },
  { id: "local", label: "local", hint: "entity vector search + one graph hop" },
  { id: "global", label: "global", hint: "community summaries only" },
] as const;

const SUGGESTIONS = [
  "Who founded Helios Labs?",
  "Which protocol does Aurora Engine use for replication between clusters?",
  "Which protocol did the Fjord Protocol replace?",
  "Who leads the platform team at Northwind Analytics?",
];

export default function QueryPage() {
  const [question, setQuestion] = useState(SUGGESTIONS[0]);
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("dual");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [history, setHistory] = useState<{ question: string; answer: Answer }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(text = question) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.query({ question: text, mode });
      setAnswer(result);
      setHistory((prev) => [{ question: text, answer: result }, ...prev].slice(0, 8));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Query</h1>
          <p>
            Dual-level retrieval: entity-level evidence from the graph, plus community
            summaries for the corpus-level view. The answer cites the documents it came from
            and reports how many of the summaries it used were out of date.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ marginBottom: 14 }}>
        <label htmlFor="question">question</label>
        <div className="row" style={{ flexWrap: "nowrap", alignItems: "stretch" }}>
          <input
            id="question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void ask();
            }}
            placeholder="Ask about the indexed corpus"
          />
          <button className="primary" disabled={busy} onClick={() => void ask()} style={{ flex: "none" }}>
            {busy ? "asking…" : "ask"}
          </button>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          {MODES.map((option) => (
            <button
              key={option.id}
              className={mode === option.id ? "" : "ghost"}
              title={option.hint}
              onClick={() => setMode(option.id)}
            >
              {option.label}
            </button>
          ))}
          <span className="spacer" />
          <span className="small muted">{MODES.find((m) => m.id === mode)?.hint}</span>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              className="ghost small"
              style={{ fontSize: 11.5 }}
              onClick={() => {
                setQuestion(suggestion);
                void ask(suggestion);
              }}
            >
              {suggestion.length > 44 ? `${suggestion.slice(0, 43)}…` : suggestion}
            </button>
          ))}
        </div>
      </div>

      {answer && (
        <div className="grid cols-2">
          <div className="card">
            <h3>Answer</h3>
            <p style={{ fontSize: 15, lineHeight: 1.65, margin: "6px 0 16px" }}>{answer.text}</p>

            <div className="row">
              <span className={`badge ${answer.stale_communities_touched === 0 ? "good" : "warn"}`}>
                {answer.stale_communities_touched === 0
                  ? "● all summaries current"
                  : `▲ ${answer.stale_communities_touched} stale summaries used`}
              </span>
              <span className="badge muted">{answer.latency_ms.toFixed(0)} ms</span>
              <span className="badge muted">{answer.mode}</span>
            </div>

            {answer.stale_communities_touched > 0 && (
              <div className="notice" style={{ marginTop: 12 }}>
                This answer used community summaries that have not caught up with a recent
                change. The query was still served — the index never blocks a read — but the
                staleness is reported rather than hidden. Run a recompute on the Maintenance
                page to clear it.
              </div>
            )}
          </div>

          <div className="card">
            <h3>Provenance</h3>
            <p className="hint">
              Documents whose passages supported this answer. Delete one and ask again — if
              it uniquely supported the fact, the answer changes.
            </p>
            {answer.citations.length ? (
              <div className="row">
                {answer.citations.map((citation) => (
                  <span className="badge" key={citation}>
                    {citation}
                  </span>
                ))}
              </div>
            ) : (
              <div className="empty">
                No sources matched — nothing in the index addresses this question.
              </div>
            )}

            <div className="grid cols-2" style={{ marginTop: 14 }}>
              <div className="stat">
                <div className="label">entities used</div>
                <div className="value">{answer.used_entities.length}</div>
              </div>
              <div className="stat">
                <div className="label">communities used</div>
                <div className="value">{answer.used_communities.length}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {history.length > 1 && (
        <div className="card" style={{ marginTop: 14 }}>
          <h3>This session</h3>
          <p className="hint">
            Ask the same question before and after a delete to see retraction take effect.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>question</th>
                  <th>answer</th>
                  <th className="num">stale</th>
                  <th>cited</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry, index) => (
                  <tr key={index}>
                    <td style={{ maxWidth: 220 }}>{entry.question}</td>
                    <td style={{ maxWidth: 380 }} className="small">
                      {entry.answer.text.slice(0, 180)}
                      {entry.answer.text.length > 180 ? "…" : ""}
                    </td>
                    <td className="num">{entry.answer.stale_communities_touched}</td>
                    <td className="small mono muted">{entry.answer.citations.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
