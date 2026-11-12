import { useEffect, useState, useCallback, useRef } from "react";
import { Stage, Layer, Image as KonvaImage, Rect } from "react-konva";
import axios from "axios";
import { frameUrl } from "./supabase";
import "./Admin.css";

const TOKEN_KEY = "admin_token";

function authHeaders() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}
const get = (path, params) => axios.get(path, { headers: authHeaders(), params }).then((r) => r.data);

// ─── Formatting helpers ────────────────────────────────────────────────────────

const fmtDate = (d) => (d ? new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—");
const fmtDay = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function timeAgo(d) {
  if (!d) return "";
  const s = Math.round((Date.now() - new Date(d).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return fmtDate(d);
}

const fmtSecs = (ms) => (ms == null ? "—" : ms >= 60000 ? `${(ms / 60000).toFixed(1)} min` : `${Math.round(ms / 1000)} s`);

// Boxes can be drawn right-to-left / bottom-to-top, so width/height may be negative.
const norm = (b) => ({
  x: Math.min(b.x, b.x + b.width),
  y: Math.min(b.y, b.y + b.height),
  w: Math.abs(b.width),
  h: Math.abs(b.height),
});

function labelBadge(img) {
  if (!img.is_labeled || !img.label) return { text: "Unlabeled", cls: "badge-pending" };
  const n = img.label.boxes?.length ?? 0;
  if (n === 0) return { text: "No otters", cls: "badge-empty" };
  return { text: `${n} otter${n === 1 ? "" : "s"}`, cls: "badge-otter" };
}

// ─── Login ─────────────────────────────────────────────────────────────────────

function LoginScreen({ onLogin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await axios.post("/api/admin/login", { password });
      sessionStorage.setItem(TOKEN_KEY, res.data.token);
      onLogin();
    } catch {
      setError("Incorrect password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="brand">🦦 Otter Labeling</div>
        <h1>Admin sign in</h1>
        <input
          type="password"
          className="login-input"
          placeholder="Admin password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        {error && <p className="login-error">{error}</p>}
        <button className="a-btn a-btn-primary" type="submit" disabled={!password || loading}>
          {loading ? "Checking…" : "Sign in"}
        </button>
        <a className="back-link" href="/">← Back to labeling</a>
      </form>
    </div>
  );
}

// ─── Thumbnail with the labeled boxes drawn on top ────────────────────────────

function Thumb({ img }) {
  const [dim, setDim] = useState(null);
  const boxes = img.label?.boxes ?? [];
  return (
    <div className="thumb">
      <img
        src={frameUrl(img.storage_path)}
        alt={img.filename}
        loading="lazy"
        onLoad={(e) => setDim([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
      />
      {dim && boxes.length > 0 && (
        <svg viewBox={`0 0 ${dim[0]} ${dim[1]}`} preserveAspectRatio="none" aria-hidden="true">
          {boxes.map((b, i) => {
            const n = norm(b);
            return <rect key={i} x={n.x} y={n.y} width={n.w} height={n.h} />;
          })}
        </svg>
      )}
    </div>
  );
}

// ─── Edit modal (admin corrections) ───────────────────────────────────────────

function EditModal({ images, startIndex, onClose, onSaved }) {
  const [index, setIndex] = useState(startIndex);
  const [konvaImage, setKonvaImage] = useState(null);
  const [boxes, setBoxes] = useState([]);
  const [drawing, setDrawing] = useState(false);
  const [currentBox, setCurrentBox] = useState(null);
  const [saving, setSaving] = useState(false);
  const [win, setWin] = useState({ w: window.innerWidth, h: window.innerHeight });

  const img = images[index];

  useEffect(() => {
    setKonvaImage(null);
    setBoxes(img.label?.boxes ?? []);
    setDrawing(false);
    setCurrentBox(null);
    const el = new window.Image();
    el.src = frameUrl(img.storage_path);
    el.onload = () => setKonvaImage(el);
  }, [img]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(i + 1, images.length - 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(i - 1, 0));
    };
    const onResize = () => setWin({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [images.length, onClose]);

  const scale = konvaImage
    ? Math.min((Math.min(win.w, 1200) - (win.w < 700 ? 32 : 140)) / konvaImage.width, (win.h - 220) / konvaImage.height)
    : 1;

  const getPos = (e) => {
    const pos = e.target.getStage().getPointerPosition();
    return { x: pos.x / scale, y: pos.y / scale };
  };
  const handleDown = (e) => {
    const pos = getPos(e);
    setDrawing(true);
    setCurrentBox({ x: pos.x, y: pos.y, width: 0, height: 0, label: "otter" });
  };
  const handleMove = (e) => {
    if (!drawing) return;
    const pos = getPos(e);
    setCurrentBox((prev) => ({ ...prev, width: pos.x - prev.x, height: pos.y - prev.y }));
  };
  const handleUp = () => {
    if (!drawing) return;
    setDrawing(false);
    if (Math.abs(currentBox.width) > 5 && Math.abs(currentBox.height) > 5) setBoxes((p) => [...p, currentBox]);
    setCurrentBox(null);
  };

  const save = async (toSave) => {
    setSaving(true);
    try {
      await axios.put(`/api/admin/images/${img.id}`, { boxes: toSave }, { headers: authHeaders() });
      setBoxes(toSave);
      onSaved(img.id, toSave);
    } finally {
      setSaving(false);
    }
  };

  const label = img.label;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-filename">{img.filename}</span>
            <span className="modal-meta">
              {label
                ? <>Labeled by <b>{label.labeler ?? "unknown"}</b> · {timeAgo(label.created_at)} · {fmtSecs(label.duration_ms)} on screen</>
                : "Not labeled yet — draw boxes to label it yourself"}
            </span>
          </div>
          <span className="modal-nav-label">{index + 1} / {images.length}</span>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-canvas-area">
          <button className="nav-arrow" onClick={() => setIndex((i) => Math.max(i - 1, 0))} disabled={index === 0} aria-label="Previous">‹</button>
          {konvaImage ? (
            <Stage
              width={konvaImage.width * scale}
              height={konvaImage.height * scale}
              onMouseDown={handleDown}
              onMouseMove={handleMove}
              onMouseUp={handleUp}
              onTouchStart={handleDown}
              onTouchMove={handleMove}
              onTouchEnd={handleUp}
              className="modal-stage"
              style={{ touchAction: "none" }}
            >
              <Layer scaleX={scale} scaleY={scale}>
                <KonvaImage image={konvaImage} />
                {boxes.map((box, i) => (
                  <Rect key={i} x={box.x} y={box.y} width={box.width} height={box.height} stroke="#f87171" strokeWidth={2 / scale} />
                ))}
                {currentBox && (
                  <Rect x={currentBox.x} y={currentBox.y} width={currentBox.width} height={currentBox.height} stroke="#facc15" strokeWidth={2 / scale} />
                )}
              </Layer>
            </Stage>
          ) : (
            <div className="modal-loading">Loading…</div>
          )}
          <button className="nav-arrow" onClick={() => setIndex((i) => Math.min(i + 1, images.length - 1))} disabled={index === images.length - 1} aria-label="Next">›</button>
        </div>

        <div className="modal-footer">
          <span className="pill">{boxes.length} {boxes.length === 1 ? "box" : "boxes"}</span>
          <div className="modal-actions">
            <button className="a-btn" onClick={() => setBoxes([])} disabled={boxes.length === 0 || saving}>Clear</button>
            <button className="a-btn a-btn-danger" onClick={() => save([])} disabled={saving}>Mark no otters</button>
            <button className="a-btn a-btn-primary" onClick={() => save(boxes)} disabled={saving}>{saving ? "Saving…" : "Save correction"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Overview ──────────────────────────────────────────────────────────────────

function ActivityChart({ perDay }) {
  const [hover, setHover] = useState(null);
  const max = Math.max(1, ...perDay.map((d) => d.labels));
  const total = perDay.reduce((s, d) => s + d.labels, 0);
  return (
    <div className="card">
      <div className="card-head">
        <h2>Labels per day</h2>
        <span className="muted">{total} in the last {perDay.length} days</span>
      </div>
      <div className="chart" role="img" aria-label={`Labels per day for the last ${perDay.length} days`}>
        <div className="chart-y"><span>{max}</span><span>0</span></div>
        <div className="chart-plot">
          <div className="chart-grid" />
          {perDay.map((d, i) => (
            <div
              key={d.date}
              className="bar-hit"
              tabIndex={0}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              aria-label={`${fmtDay(d.date)}: ${d.labels} labels`}
            >
              <div className={`bar ${hover === i ? "active" : ""}`} style={{ height: `${(d.labels / max) * 100}%` }} />
              {hover === i && (
                <div className={`tip ${i > perDay.length / 2 ? "tip-left" : ""}`}>
                  <span className="tip-k">{fmtDay(d.date)}</span>
                  <span className="tip-v">{d.labels} label{d.labels === 1 ? "" : "s"}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="chart-x">
        <span>{fmtDay(perDay[0].date)}</span>
        <span>{fmtDay(perDay[Math.floor(perDay.length / 2)].date)}</span>
        <span>Today</span>
      </div>
      <table className="sr-only">
        <caption>Labels per day</caption>
        <tbody>{perDay.map((d) => <tr key={d.date}><th>{d.date}</th><td>{d.labels}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function Overview({ ov, onOpenPhotos, onOpenVolunteer }) {
  const [recent, setRecent] = useState(null);
  const [top, setTop] = useState(null);

  useEffect(() => {
    get("/api/admin/images", { filter: "labeled", page_size: 8 }).then((r) => setRecent(r.items));
    get("/api/admin/volunteers").then((r) => setTop(r.slice(0, 5)));
  }, [ov]);

  return (
    <>
      <div className="stats">
        <div className="stat stat-wide">
          <div className="stat-k">Labeled</div>
          <div className="stat-v">{ov.labeled.toLocaleString()} <span className="stat-of">of {ov.total.toLocaleString()}</span></div>
          <div className="meter" aria-hidden="true"><div style={{ width: `${ov.percent_complete}%` }} /></div>
          <div className="stat-sub">{ov.percent_complete}% complete · {ov.unlabeled.toLocaleString()} to go</div>
        </div>
        <div className="stat">
          <div className="stat-k">With otters</div>
          <div className="stat-v">{ov.with_otters.toLocaleString()}</div>
          <div className="stat-sub">{ov.labeled ? Math.round((ov.with_otters / ov.labeled) * 100) : 0}% of labeled frames</div>
        </div>
        <div className="stat">
          <div className="stat-k">Volunteers</div>
          <div className="stat-v">{ov.volunteers}</div>
          <div className="stat-sub">{ov.active_7d} active this week</div>
        </div>
        <div className="stat">
          <div className="stat-k">Service hours</div>
          <div className="stat-v">{ov.hours}</div>
          <div className="stat-sub">all volunteers combined</div>
        </div>
      </div>

      <div className="overview-grid">
        <ActivityChart perDay={ov.per_day} />

        <div className="card">
          <div className="card-head">
            <h2>Top volunteers</h2>
            <button className="link-btn" onClick={() => onOpenVolunteer(null)}>See all</button>
          </div>
          {top === null ? <p className="muted">Loading…</p> : top.length === 0 ? <p className="muted">No volunteers yet.</p> : (
            <ol className="top-list">
              {top.map((v, i) => (
                <li key={v.user_id}>
                  <button onClick={() => onOpenVolunteer(v)}>
                    <span className="rank">{i + 1}</span>
                    <span className="who"><b>{v.full_name}</b><small>{v.email}</small></span>
                    <span className="num">{v.labels}<small>{v.hours} h</small></span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Recently labeled</h2>
          <button className="link-btn" onClick={onOpenPhotos}>Browse all photos</button>
        </div>
        {recent === null ? <p className="muted">Loading…</p> : recent.length === 0 ? <p className="muted">Nothing labeled yet.</p> : (
          <div className="recent-strip">
            {recent.map((img) => {
              const b = labelBadge(img);
              return (
                <div key={img.id} className="recent-item">
                  <Thumb img={img} />
                  <div className="recent-meta">
                    <span className={`badge ${b.cls}`}>{b.text}</span>
                    <span className="muted">{img.label?.labeler} · {timeAgo(img.label?.created_at)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

// ─── Photos ────────────────────────────────────────────────────────────────────

const FILTERS = [
  ["labeled", "Labeled"],
  ["otters", "With otters"],
  ["empty", "No otters"],
  ["unlabeled", "Unlabeled"],
  ["all", "All"],
];
const PAGE_SIZE = 48;

function Photos({ volunteers, userFilter, setUserFilter, onChanged }) {
  const [filter, setFilter] = useState("labeled");
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [editIndex, setEditIndex] = useState(null);
  const topRef = useRef(null);

  // A volunteer's photos are always labeled ones.
  const effectiveFilter = userFilter && filter === "unlabeled" ? "labeled" : filter;

  useEffect(() => {
    let live = true;
    setData(null);
    get("/api/admin/images", { filter: effectiveFilter, user_id: userFilter || undefined, page, page_size: PAGE_SIZE })
      .then((r) => live && setData(r));
    return () => { live = false; };
  }, [effectiveFilter, userFilter, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const goPage = (p) => { setPage(p); topRef.current?.scrollIntoView({ behavior: "smooth" }); };

  const handleSaved = useCallback((imageId, boxes) => {
    setData((d) => d && {
      ...d,
      items: d.items.map((img) => img.id !== imageId ? img : {
        ...img,
        is_labeled: true,
        label: { ...(img.label ?? { labeler: "Admin", created_at: new Date().toISOString() }), boxes, has_otter: boxes.length > 0 },
      }),
    });
    onChanged();
  }, [onChanged]);

  return (
    <div ref={topRef}>
      <div className="toolbar">
        <div className="seg" role="tablist">
          {FILTERS.map(([k, label]) => (
            <button
              key={k}
              role="tab"
              aria-selected={effectiveFilter === k}
              className={effectiveFilter === k ? "on" : ""}
              disabled={!!userFilter && k === "unlabeled"}
              onClick={() => { setFilter(k); setPage(0); }}
            >{label}</button>
          ))}
        </div>
        <select className="a-select" value={userFilter ?? ""} onChange={(e) => { setUserFilter(e.target.value || null); setPage(0); }}>
          <option value="">All volunteers</option>
          {volunteers.map((v) => <option key={v.user_id} value={v.user_id}>{v.full_name} ({v.labels})</option>)}
        </select>
        <span className="muted toolbar-count">{data ? `${data.total.toLocaleString()} photos` : "Loading…"}</span>
      </div>

      {data && data.items.length === 0 && <div className="empty-state">No photos match this filter.</div>}

      <div className="photo-grid">
        {(data?.items ?? Array.from({ length: 12 }, () => null)).map((img, i) =>
          img ? (
            <button key={img.id} className="photo-card" onClick={() => setEditIndex(i)}>
              <Thumb img={img} />
              <div className="photo-meta">
                <span className={`badge ${labelBadge(img).cls}`}>{labelBadge(img).text}</span>
                {img.label
                  ? <span className="photo-who">{img.label.labeler} · {timeAgo(img.label.created_at)}</span>
                  : <span className="photo-who">{img.filename.replace(/\.jpg$/, "")}</span>}
              </div>
            </button>
          ) : <div key={i} className="photo-card skeleton" />
        )}
      </div>

      {data && pages > 1 && (
        <div className="pager">
          <button className="a-btn" disabled={page === 0} onClick={() => goPage(page - 1)}>← Prev</button>
          <span className="muted">Page {page + 1} of {pages}</span>
          <button className="a-btn" disabled={page >= pages - 1} onClick={() => goPage(page + 1)}>Next →</button>
        </div>
      )}

      {editIndex !== null && data && (
        <EditModal images={data.items} startIndex={editIndex} onClose={() => setEditIndex(null)} onSaved={handleSaved} />
      )}
    </div>
  );
}

// ─── Volunteers ────────────────────────────────────────────────────────────────

function downloadCsv(rows) {
  const cols = [
    ["full_name", "Name"], ["email", "Email"], ["organization", "Organization"],
    ["labels", "Images labeled"], ["labels_with_otters", "With otters"], ["hours", "Hours"],
    ["first_label_at", "First label"], ["last_label_at", "Last label"], ["joined_at", "Joined"],
  ];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(([, h]) => esc(h)).join(",")];
  for (const r of rows) {
    lines.push(cols.map(([k]) => esc(k.endsWith("_at") ? (r[k] ? new Date(r[k]).toLocaleDateString() : "") : r[k])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `otter-volunteer-hours-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function Volunteers({ rows, onOpen }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("labels");

  if (!rows) return <p className="muted">Loading volunteers…</p>;

  const needle = q.trim().toLowerCase();
  const shown = rows
    .filter((r) => !needle || [r.full_name, r.email, r.organization].some((v) => v?.toLowerCase().includes(needle)))
    .sort((a, b) => sort === "name"
      ? (a.full_name || "").localeCompare(b.full_name || "")
      : sort === "recent"
        ? new Date(b.last_label_at || 0) - new Date(a.last_label_at || 0)
        : (b[sort] ?? 0) - (a[sort] ?? 0));
  const showOrg = rows.some((r) => r.organization);

  const th = (key, label, cls = "") => (
    <th className={cls}>
      <button className={`sort ${sort === key ? "on" : ""}`} onClick={() => setSort(key)}>{label}{sort === key ? " ↓" : ""}</button>
    </th>
  );

  return (
    <div>
      <div className="toolbar">
        <input className="a-input" placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="muted toolbar-count">{shown.length} volunteer{shown.length === 1 ? "" : "s"}</span>
        <button className="a-btn" onClick={() => downloadCsv(shown)} disabled={!shown.length}>Download hours CSV</button>
      </div>
      <div className="table-wrap">
        <table className="vol-table">
          <thead>
            <tr>
              {th("name", "Name")}
              {showOrg && <th>Organization</th>}
              {th("labels", "Labeled", "num")}
              {th("labels_with_otters", "With otters", "num")}
              {th("hours", "Hours", "num")}
              <th>Joined</th>
              {th("recent", "Last active")}
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={8} className="muted">No volunteers match.</td></tr>}
            {shown.map((r) => (
              <tr key={r.user_id} onClick={() => onOpen(r)}>
                <td><div className="who"><b>{r.full_name || "—"}</b><small>{r.email}</small></div></td>
                {showOrg && <td>{r.organization}</td>}
                <td className="num">{r.labels}</td>
                <td className="num">{r.labels_with_otters}</td>
                <td className="num">{r.hours}</td>
                <td>{fmtDate(r.joined_at)}</td>
                <td>{r.last_label_at ? timeAgo(r.last_label_at) : <span className="muted">never</span>}</td>
                <td className="num"><span className="link-btn">Photos →</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted footnote">Hours = time each image was on screen until submit, capped at 2 minutes per image.</p>
    </div>
  );
}

// ─── Admin shell ───────────────────────────────────────────────────────────────

export default function Admin() {
  const [authed, setAuthed] = useState(!!sessionStorage.getItem(TOKEN_KEY));
  const [tab, setTab] = useState("overview");
  const [ov, setOv] = useState(null);
  const [volunteers, setVolunteers] = useState(null);
  const [userFilter, setUserFilter] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    get("/api/admin/overview").then(setOv).catch((e) => {
      if (e.response?.status === 401) { sessionStorage.removeItem(TOKEN_KEY); setAuthed(false); }
      else setError("Couldn't load admin data. Refresh to try again.");
    });
    get("/api/admin/volunteers").then(setVolunteers).catch(() => {});
  }, []);

  useEffect(() => { if (authed) refresh(); }, [authed, refresh]);

  if (!authed) return <LoginScreen onLogin={() => setAuthed(true)} />;

  const logout = () => { sessionStorage.removeItem(TOKEN_KEY); setAuthed(false); };
  const openVolunteer = (v) => {
    if (v) { setUserFilter(v.user_id); setTab("photos"); } else setTab("volunteers");
  };
  const selectedVol = volunteers?.find((v) => v.user_id === userFilter);

  return (
    <div className="admin">
      <header className="admin-top">
        <div className="brand">🦦 Otter Labeling <span className="brand-tag">Admin</span></div>
        <nav className="tabs">
          {[["overview", "Overview"], ["photos", "Photos"], ["volunteers", "Volunteers"]].map(([k, label]) => (
            <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        <div className="top-links">
          <a className="back-link" href="/">Labeling site</a>
          <button className="a-btn a-btn-ghost" onClick={logout}>Log out</button>
        </div>
      </header>

      <main className="admin-main">
        {error && <p className="error-banner">{error}</p>}

        {tab === "photos" && selectedVol && (
          <div className="vol-banner">
            Showing photos labeled by <b>{selectedVol.full_name}</b> · {selectedVol.labels} labeled · {selectedVol.hours} h
            <button className="link-btn" onClick={() => setUserFilter(null)}>Clear</button>
          </div>
        )}

        {tab === "overview" && (ov
          ? <Overview ov={ov} onOpenPhotos={() => { setUserFilter(null); setTab("photos"); }} onOpenVolunteer={openVolunteer} />
          : <p className="muted">Loading…</p>)}
        {tab === "photos" && (
          <Photos volunteers={volunteers ?? []} userFilter={userFilter} setUserFilter={setUserFilter} onChanged={refresh} />
        )}
        {tab === "volunteers" && <Volunteers rows={volunteers} onOpen={openVolunteer} />}
      </main>
    </div>
  );
}
