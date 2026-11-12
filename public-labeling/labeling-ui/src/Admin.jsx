import { useEffect, useState, useCallback } from "react";
import { Stage, Layer, Image as KonvaImage, Rect } from "react-konva";
import axios from "axios";
import { frameUrl } from "./supabase";
import "./Admin.css";

const BACKEND = "";

const TOKEN_KEY = "admin_token";

function authHeaders() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ─── Login Screen ──────────────────────────────────────────────────────────────

function LoginScreen({ onLogin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await axios.post(`${BACKEND}/api/admin/login`, { password });
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
        <h1>Admin Login</h1>
        <input
          type="password"
          className="login-input"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        {error && <p className="login-error">{error}</p>}
        <button className="btn-submit login-btn" type="submit" disabled={!password || loading}>
          {loading ? "Checking…" : "Enter"}
        </button>
      </form>
    </div>
  );
}

function imageUrl(storage_path) {
  return frameUrl(storage_path);
}

// ─── Edit Modal ────────────────────────────────────────────────────────────────

function EditModal({ images, startIndex, onClose, onSaved }) {
  const [index, setIndex] = useState(startIndex);
  const [konvaImage, setKonvaImage] = useState(null);
  const [boxes, setBoxes] = useState([]);
  const [drawing, setDrawing] = useState(false);
  const [currentBox, setCurrentBox] = useState(null);
  const [saving, setSaving] = useState(false);

  const img = images[index];

  // Load image + prefill existing boxes whenever the selected image changes
  useEffect(() => {
    setKonvaImage(null);
    setBoxes(img.labels?.[0]?.boxes ?? []);
    setDrawing(false);
    setCurrentBox(null);

    const el = new window.Image();
    el.src = imageUrl(img.storage_path);
    el.onload = () => setKonvaImage(el);
  }, [index]);

  // Close on Escape, navigate on arrow keys
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(i + 1, images.length - 1));
      if (e.key === "ArrowLeft")  setIndex((i) => Math.max(i - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [images.length, onClose]);

  const scale = konvaImage
    ? Math.min((window.innerWidth * 0.85) / konvaImage.width,
               (window.innerHeight * 0.78) / konvaImage.height, 1)
    : 1;

  const getPos = (e) => {
    const pos = e.target.getStage().getPointerPosition();
    return { x: pos.x / scale, y: pos.y / scale };
  };

  const handleMouseDown = (e) => {
    const pos = getPos(e);
    setDrawing(true);
    setCurrentBox({ x: pos.x, y: pos.y, width: 0, height: 0, label: "otter" });
  };

  const handleMouseMove = (e) => {
    if (!drawing) return;
    const pos = getPos(e);
    setCurrentBox((prev) => ({ ...prev, width: pos.x - prev.x, height: pos.y - prev.y }));
  };

  const handleMouseUp = () => {
    if (!drawing) return;
    setDrawing(false);
    if (Math.abs(currentBox.width) > 5 && Math.abs(currentBox.height) > 5) {
      setBoxes((prev) => [...prev, currentBox]);
    }
    setCurrentBox(null);
  };

  const handleSave = async () => {
    setSaving(true);
    await axios.put(`${BACKEND}/api/admin/images/${img.id}`, { boxes }, { headers: authHeaders() });
    onSaved(img.id, boxes);
    setSaving(false);
  };

  const stageW = konvaImage ? konvaImage.width * scale : 0;
  const stageH = konvaImage ? konvaImage.height * scale : 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>

        <div className="modal-header">
          <span className="modal-filename">{img.filename}</span>
          <span className="modal-nav-label">{index + 1} / {images.length}</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-canvas-area">
          <button
            className="nav-arrow nav-prev"
            onClick={() => setIndex((i) => Math.max(i - 1, 0))}
            disabled={index === 0}
          >‹</button>

          {konvaImage ? (
            <Stage
              width={stageW}
              height={stageH}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onTouchStart={handleMouseDown}
              onTouchMove={handleMouseMove}
              onTouchEnd={handleMouseUp}
              style={{ cursor: "crosshair", borderRadius: 8, overflow: "hidden", touchAction: "none" }}
            >
              <Layer scaleX={scale} scaleY={scale}>
                <KonvaImage image={konvaImage} />
                {boxes.map((box, i) => (
                  <Rect key={i} x={box.x} y={box.y} width={box.width} height={box.height}
                    stroke="#ef4444" strokeWidth={2 / scale} />
                ))}
                {currentBox && (
                  <Rect x={currentBox.x} y={currentBox.y} width={currentBox.width} height={currentBox.height}
                    stroke="#facc15" strokeWidth={2 / scale} />
                )}
              </Layer>
            </Stage>
          ) : (
            <div className="modal-loading">Loading…</div>
          )}

          <button
            className="nav-arrow nav-next"
            onClick={() => setIndex((i) => Math.min(i + 1, images.length - 1))}
            disabled={index === images.length - 1}
          >›</button>
        </div>

        <div className="modal-footer">
          <span className="modal-box-count">{boxes.length} {boxes.length === 1 ? "box" : "boxes"}</span>
          <div className="modal-actions">
            <button className="btn-clear" onClick={() => setBoxes([])} disabled={boxes.length === 0}>
              Clear Boxes
            </button>
            <button className="btn-no-otters" onClick={async () => { setBoxes([]); await axios.put(`${BACKEND}/api/admin/images/${img.id}`, { boxes: [] }, { headers: authHeaders() }); onSaved(img.id, []); }}>
              No Otters
            </button>
            <button className="btn-submit" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}

// ─── Volunteers (community-service hours) ──────────────────────────────────────

const fmtDate = (d) => (d ? new Date(d).toLocaleDateString() : "");

function downloadCsv(rows) {
  const cols = [
    ["full_name", "Name"], ["email", "Email"], ["organization", "Organization"],
    ["labels", "Images labeled"], ["hours", "Hours"],
    ["first_label_at", "First label"], ["last_label_at", "Last label"], ["joined_at", "Joined"],
  ];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(([, h]) => esc(h)).join(",")];
  for (const r of rows) {
    lines.push(cols.map(([k]) => esc(k.endsWith("_at") ? fmtDate(r[k]) : r[k])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `otter-volunteer-hours-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function Volunteers() {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    axios.get(`${BACKEND}/api/admin/volunteers`, { headers: authHeaders() }).then((r) => setRows(r.data));
  }, []);

  if (!rows) return <p className="vol-muted">Loading volunteers…</p>;

  const needle = q.trim().toLowerCase();
  const shown = needle
    ? rows.filter((r) => [r.full_name, r.email, r.organization].some((v) => v?.toLowerCase().includes(needle)))
    : rows;

  return (
    <div>
      <div className="filter-row">
        <input className="vol-search" placeholder="Search name, email, organization" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="filter-count">{shown.length} volunteers</span>
        <button className="filter-btn" onClick={() => downloadCsv(shown)} disabled={!shown.length}>Download CSV</button>
      </div>
      <div className="vol-table-wrap">
        <table className="vol-table">
          <thead>
            <tr><th>Name</th><th>Email</th><th>Organization</th><th className="num">Labeled</th><th className="num">Hours</th><th>First</th><th>Last</th></tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.user_id}>
                <td>{r.full_name}</td>
                <td>{r.email}</td>
                <td>{r.organization}</td>
                <td className="num">{r.labels}</td>
                <td className="num">{r.hours}</td>
                <td>{fmtDate(r.first_label_at)}</td>
                <td>{fmtDate(r.last_label_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Admin Page ────────────────────────────────────────────────────────────────

export default function Admin() {
  const [authed, setAuthed] = useState(!!sessionStorage.getItem(TOKEN_KEY));
  const [stats, setStats] = useState(null);
  const [images, setImages] = useState([]);
  const [filter, setFilter] = useState("all");
  const [editIndex, setEditIndex] = useState(null);
  const [tab, setTab] = useState("images");

  const fetchData = () => {
    const h = { headers: authHeaders() };
    axios.get(`${BACKEND}/api/admin/stats`, h).then((r) => setStats(r.data));
    axios.get(`${BACKEND}/api/admin/images`, h).then((r) => setImages(r.data));
  };

  useEffect(() => { if (authed) fetchData(); }, [authed]);

  if (!authed) return <LoginScreen onLogin={() => setAuthed(true)} />;

  const filtered = images.filter((img) => {
    if (filter === "labeled") return img.is_labeled;
    if (filter === "unlabeled") return !img.is_labeled;
    return true;
  });

  const getLabelInfo = (img) => {
    if (!img.is_labeled) return { text: "Pending", cls: "badge-pending" };
    const boxes = img.labels?.[0]?.boxes ?? [];
    if (boxes.length === 0) return { text: "No Otters", cls: "badge-no-otters" };
    return { text: `${boxes.length} ${boxes.length === 1 ? "box" : "boxes"}`, cls: "badge-done" };
  };

  // Update local state after a save so the grid refreshes without a full reload
  const handleSaved = useCallback((imageId, newBoxes) => {
    setImages((prev) =>
      prev.map((img) =>
        img.id !== imageId ? img : {
          ...img,
          is_labeled: true,
          labels: [{ boxes: newBoxes }],
        }
      )
    );
    axios.get(`${BACKEND}/api/admin/stats`, { headers: authHeaders() }).then((r) => setStats(r.data));
  }, []);

  const handleLogout = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setAuthed(false);
  };

  return (
    <div className="admin">
      <div className="admin-header">
        <h1>Admin — Labeling Progress</h1>
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <a className="back-link" href="/">← Back to labeling</a>
          <button className="logout-btn" onClick={handleLogout}>Log out</button>
        </div>
      </div>

      {stats && (
        <div className="stats-row">
          <div className="stat-card">
            <div className="stat-value">{stats.total}</div>
            <div className="stat-label">Total Images</div>
          </div>
          <div className="stat-card labeled">
            <div className="stat-value">{stats.labeled}</div>
            <div className="stat-label">Labeled</div>
          </div>
          <div className="stat-card unlabeled">
            <div className="stat-value">{stats.unlabeled}</div>
            <div className="stat-label">Remaining</div>
          </div>
          <div className="stat-card percent">
            <div className="stat-value">{stats.percent_complete}%</div>
            <div className="stat-label">Complete</div>
          </div>
        </div>
      )}

      {stats && (
        <div className="progress-bar-wrap">
          <div className="progress-bar-fill" style={{ width: `${stats.percent_complete}%` }} />
        </div>
      )}

      <div className="filter-row tab-row">
        {[["images", "Images"], ["volunteers", "Volunteers"]].map(([k, label]) => (
          <button key={k} className={`filter-btn ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "volunteers" ? <Volunteers /> : <>
      <div className="filter-row">
        {["all", "labeled", "unlabeled"].map((f) => (
          <button key={f} className={`filter-btn ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
        <span className="filter-count">{filtered.length} images</span>
      </div>

      <div className="image-grid">
        {filtered.map((img, i) => {
          const label = getLabelInfo(img);
          return (
            <div key={img.id} className={`image-card ${img.is_labeled ? "done" : ""}`}
              onClick={() => setEditIndex(i)}>
              <img src={imageUrl(img.storage_path)} alt={img.filename} />
              <div className="image-card-footer">
                <span className="image-name">{img.filename}</span>
                <span className={`badge ${label.cls}`}>{label.text}</span>
              </div>
            </div>
          );
        })}
      </div>

      </>}

      {editIndex !== null && (
        <EditModal
          images={filtered}
          startIndex={editIndex}
          onClose={() => setEditIndex(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
