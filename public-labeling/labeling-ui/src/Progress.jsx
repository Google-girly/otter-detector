import { useEffect, useState } from "react";
import { api } from "./supabase";

// Slide-over panel: the volunteer's own totals + the top-10 leaderboard.
export default function Progress({ me, onClose, onSignOut }) {
  const [board, setBoard] = useState(null);

  useEffect(() => {
    api.get("/api/leaderboard").then((r) => setBoard(r.data)).catch(() => setBoard([]));
  }, []);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const since = me?.first_label_at
    ? new Date(me.first_label_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    : null;

  return (
    <div className="progress-overlay" onClick={onClose}>
      <aside className="progress-panel" onClick={(e) => e.stopPropagation()}>
        <div className="progress-head">
          <div>
            <div className="progress-name">{me?.full_name ?? "…"}</div>
            <div className="progress-email">{me?.email}{me?.organization ? ` · ${me.organization}` : ""}</div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="my-stats">
          <div className="my-stat"><div className="v">{me?.labels ?? "–"}</div><div className="k">Images labeled</div></div>
          <div className="my-stat"><div className="v">{me?.hours ?? "–"}</div><div className="k">Hours</div></div>
          <div className="my-stat"><div className="v">{me?.labels_today ?? "–"}</div><div className="k">Today</div></div>
          <div className="my-stat"><div className="v">{me?.rank ? `#${me.rank}` : "–"}</div><div className="k">Rank</div></div>
        </div>
        <p className="hours-note">
          Hours count the time each image is on screen until you submit, capped at 2 minutes per image.
          {since && ` Labeling since ${since}.`}
        </p>

        <h3>Top volunteers</h3>
        {board === null ? (
          <p className="muted">Loading…</p>
        ) : board.length === 0 ? (
          <p className="muted">No labels yet. Be the first!</p>
        ) : (
          <ol className="leaderboard">
            {board.map((r) => (
              <li key={r.rank} className={r.is_me ? "me" : ""}>
                <span className="lb-rank">{r.rank}</span>
                <span className="lb-name">{r.name}{r.is_me ? " (you)" : ""}</span>
                <span className="lb-count">{r.labels}</span>
              </li>
            ))}
          </ol>
        )}

        <button className="btn-clear signout" onClick={onSignOut}>Sign out</button>
      </aside>
    </div>
  );
}
