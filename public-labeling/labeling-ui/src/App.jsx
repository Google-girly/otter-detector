import { useEffect, useState, useRef } from "react";
import { Stage, Layer, Image, Rect } from "react-konva";
import { supabase, api, frameUrl } from "./supabase";
import Auth from "./Auth";
import Progress from "./Progress";
import "./App.css";

// Session gate: signed out -> Auth, signed in -> Labeler.
export default function App() {
  const [session, setSession] = useState(undefined); // undefined = still checking
  const [recovering, setRecovering] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      if (event === "PASSWORD_RECOVERY") setRecovering(true);
      setSession(s);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  if (session === undefined) return <p className="loading">Loading…</p>;
  if (recovering) return <Auth initialMode="reset" onDone={() => setRecovering(false)} />;
  if (!session) return <Auth />;
  return <Labeler key={session.user.id} />;
}

function Labeler() {
  const [image, setImage] = useState(null);
  const [konvaImage, setKonvaImage] = useState(null);
  const [boxes, setBoxes] = useState([]);
  const [drawing, setDrawing] = useState(false);
  const [currentBox, setCurrentBox] = useState(null);
  const [selectedBox, setSelectedBox] = useState(null);
  const [me, setMe] = useState(null);
  const [showProgress, setShowProgress] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const shownAt = useRef(0); // when the current image appeared, for hours tracking
  const areaRef = useRef(null); // the box the image has to fit in
  const [viewport, setViewport] = useState({ w: 0, h: 0 });

  const refreshMe = () => api.get("/api/me").then((r) => setMe(r.data)).catch(() => {});

  const fetchNextImage = async () => {
    setBoxes([]);
    setSelectedBox(null);
    setError("");
    let res;
    try {
      res = await api.get("/api/next-image");
    } catch {
      setError("Couldn't load the next image. Check your connection and refresh.");
      return;
    }
    if (!res.data.id) {
      setAllDone(true);
      setKonvaImage(null);
      return;
    }
    setImage(res.data);

    const img = new window.Image();
    img.src = frameUrl(res.data.storage_path);
    img.onload = () => {
      setKonvaImage(img);
      shownAt.current = Date.now();
    };
  };

  useEffect(() => {
    fetchNextImage();
    refreshMe();
  }, []);

  // Re-fit the image whenever the window resizes or the phone rotates.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const update = () => setViewport({ w: el.clientWidth, h: window.innerHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [konvaImage]);

  // Fill the available width, but leave room below for the buttons. Boxes are
  // stored in original-image pixels, so changing the scale never moves them.
  const RESERVED_H = viewport.w < 600 ? 260 : 220; // header + instructions + buttons
  const scale = konvaImage && viewport.w
    ? Math.min(
        viewport.w / konvaImage.width,
        Math.max(viewport.h - RESERVED_H, 180) / konvaImage.height,
      )
    : 0;

  // Delete selected box with keyboard
  useEffect(() => {
    const handler = (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && selectedBox !== null) {
        setBoxes((prev) => prev.filter((_, i) => i !== selectedBox));
        setSelectedBox(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selectedBox]);

  const getPos = (e) => {
    const pos = e.target.getStage().getPointerPosition();
    return { x: pos.x / scale, y: pos.y / scale };
  };

  const handleMouseDown = (e) => {
    if (e.target.getClassName() === "Rect") return;
    setSelectedBox(null);
    const pos = getPos(e);
    setDrawing(true);
    setCurrentBox({ x: pos.x, y: pos.y, width: 0, height: 0, label: "otter" });
  };

  const handleMouseMove = (e) => {
    if (!drawing) return;
    const pos = getPos(e);
    setCurrentBox((prev) => ({
      ...prev,
      width: pos.x - prev.x,
      height: pos.y - prev.y,
    }));
  };

  const handleTouchStart = (e) => {
    if (e.evt.touches.length > 1) return;
    handleMouseDown(e);
  };

  const handleTouchMove = (e) => {
    if (e.evt.touches.length > 1) return;
    handleMouseMove(e);
  };

  // When the user releases, save the box
  const handleMouseUp = () => {
    if (!drawing) return;
    setDrawing(false);
    if (Math.abs(currentBox.width) > 5 && Math.abs(currentBox.height) > 5) {
      setBoxes((prev) => [...prev, currentBox]);
    }
    setCurrentBox(null);
  };

  // Submit boxes (an empty list means "no otters in this frame")
  const submit = async (boxesToSend) => {
    setSubmitting(true);
    try {
      await api.post("/api/submit-label", {
        image_id: image.id,
        boxes: boxesToSend,
        duration_ms: Date.now() - shownAt.current,
      });
      // Optimistic bump so the counter feels instant; /api/me corrects it.
      setMe((m) => m && { ...m, labels: m.labels + 1, labels_today: m.labels_today + 1 });
      refreshMe();
    } catch (err) {
      // 409 = someone else labeled it first; just move on without credit.
      if (err.response?.status !== 409) {
        setError("That label didn't save. Please try again.");
        setSubmitting(false);
        return;
      }
    }
    setSubmitting(false);
    fetchNextImage();
  };

  const handleSubmit = () => submit(boxes);
  const handleNoOtters = () => submit([]);

  const header = (
    <div className="header">
      <div className="brand">🦦 Otter Labeling</div>
      <div className="header-right">
        <button className="me-pill" onClick={() => setShowProgress(true)} title="Your progress">
          <span>{me ? me.labels : "–"}</span> labeled
          {me && <> · <span>{me.hours}</span> hrs</>}
        </button>
        <button className="btn-logout" onClick={() => supabase.auth.signOut()}>Log out</button>
      </div>
    </div>
  );

  const progress = showProgress && (
    <Progress me={me} onClose={() => setShowProgress(false)} onSignOut={() => supabase.auth.signOut()} />
  );

  if (allDone) {
    return (
      <div className="app">
        {header}
        <div className="done-card">
          <h2>No more pictures 🎉</h2>
          <p>Every picture has been labeled. Thank you! New footage is added regularly, so check back soon.</p>
          {me && (
            <p>
              You've labeled <b>{me.labels}</b> picture{me.labels === 1 ? "" : "s"} · <b>{me.hours}</b> hour{me.hours === 1 ? "" : "s"} of service
            </p>
          )}
          <button className="btn-submit" onClick={() => { setAllDone(false); fetchNextImage(); }}>Check again</button>
        </div>
        {progress}
      </div>
    );
  }

  if (error && !konvaImage) return <div className="app">{header}<p className="loading">{error}</p>{progress}</div>;

  if (!konvaImage) return <div className="app">{header}<p className="loading">Loading image…</p>{progress}</div>;

  const stageW = konvaImage.width * scale;
  const stageH = konvaImage.height * scale;

  return (
    <div className="app">
      {header}
      <div className="instructions">
        <p>Drag a box around <b>each otter</b>, then press <b>Submit</b>. Nothing there? Press <b>No otters</b>.</p>
        <span className="box-count"><span>{boxes.length}</span> {boxes.length === 1 ? "box" : "boxes"}</span>
      </div>

      <div className="canvas-area" ref={areaRef}>
      {scale > 0 && (
      <div className="canvas-wrapper">
        <Stage
          width={stageW}
          height={stageH}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleMouseUp}
          style={{ touchAction: "none" }}
        >
          <Layer scaleX={scale} scaleY={scale}>
            <Image image={konvaImage} />

            {boxes.map((box, i) => (
              <Rect
                key={i}
                x={box.x}
                y={box.y}
                width={box.width}
                height={box.height}
                stroke={selectedBox === i ? "#facc15" : "#ef4444"}
                fill={selectedBox === i ? "rgba(250, 204, 21, 0.15)" : "transparent"}
                strokeWidth={2 / scale}
                onClick={(e) => {
                  e.cancelBubble = true;
                  setSelectedBox(selectedBox === i ? null : i);
                }}
                onTap={(e) => {
                  e.cancelBubble = true;
                  setSelectedBox(selectedBox === i ? null : i);
                }}
              />
            ))}

            {currentBox && (
              <Rect
                x={currentBox.x}
                y={currentBox.y}
                width={currentBox.width}
                height={currentBox.height}
                stroke="#facc15"
                strokeWidth={2 / scale}
              />
            )}
          </Layer>
        </Stage>
      </div>
      )}
      </div>


      <div className="actions">
        <div className="actions-edit">
          <button
            className="btn-ghost"
            onClick={() => { setBoxes((prev) => prev.filter((_, i) => i !== selectedBox)); setSelectedBox(null); }}
            disabled={selectedBox === null}
            title="Tap a box to select it, then delete (or press Delete)"
          >
            Delete selected
          </button>
          <button className="btn-ghost" onClick={() => { setBoxes([]); setSelectedBox(null); }} disabled={boxes.length === 0}>
            Clear all
          </button>
        </div>
        <div className="actions-main">
          <button className="btn-no-otters" onClick={handleNoOtters} disabled={submitting || boxes.length > 0}>
            No otters
          </button>
          <button className="btn-submit" onClick={handleSubmit} disabled={boxes.length === 0 || submitting}>
            {submitting ? "Saving…" : `Submit${boxes.length ? ` ${boxes.length}` : ""}`}
          </button>
        </div>
      </div>
      {error && <p className="inline-error">{error}</p>}
      {progress}
    </div>
  );
}