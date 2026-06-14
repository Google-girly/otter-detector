import { useEffect, useState, useRef } from "react";
import { Stage, Layer, Image, Rect } from "react-konva";
import axios from "axios";
import "./App.css";

const SUPABASE_URL = "https://ofuqvgfdninllnlhtqij.supabase.co";
const BACKEND = "";
const LABELS = ["otter", "not sure"];

export default function App() {
  const [image, setImage] = useState(null);
  const [konvaImage, setKonvaImage] = useState(null);
  const [scale, setScale] = useState(1);
  const [boxes, setBoxes] = useState([]);
  const [drawing, setDrawing] = useState(false);
  const [currentBox, setCurrentBox] = useState(null);
  const [selectedBox, setSelectedBox] = useState(null);

  const fetchNextImage = async () => {
    setBoxes([]);
    setSelectedBox(null);
    const res = await axios.get(`${BACKEND}/api/next-image`);
    setImage(res.data);

    const img = new window.Image();
    img.src = `${SUPABASE_URL}/storage/v1/object/public/frames/${res.data.storage_path}`;
    img.onload = () => {
      const maxW = window.innerWidth - 40;
      const maxH = window.innerHeight - 160;
      setScale(Math.min(maxW / img.width, maxH / img.height, 1));
      setKonvaImage(img);
    };
  };

  useEffect(() => {
    fetchNextImage();
  }, []);

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

  // Submit all boxes to the backend
  const handleSubmit = async () => {
    await axios.post(`${BACKEND}/api/submit-label`, {
      image_id: image.id,
      boxes: boxes,
      labeled_by: "",
    });
    fetchNextImage();
  };

  // Submit with no boxes — marks image as labeled with empty annotation
  const handleNoOtters = async () => {
    await axios.post(`${BACKEND}/api/submit-label`, {
      image_id: image.id,
      boxes: [],
      labeled_by: "",
    });
    fetchNextImage();
  };

  if (!konvaImage) return <p className="loading">Loading image...</p>;

  const stageW = konvaImage.width * scale;
  const stageH = konvaImage.height * scale;

  return (
    <div className="app">
      <div className="header">
        <h1>Otter Labeling</h1>
        <div className="box-count">
          <span>{boxes.length}</span> {boxes.length === 1 ? "box" : "boxes"} drawn
        </div>
      </div>

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


      <div className="actions">
        <button className="btn-submit" onClick={handleSubmit} disabled={boxes.length === 0}>
          Submit Labels
        </button>
        <button
          className="btn-delete-selected"
          onClick={() => { setBoxes((prev) => prev.filter((_, i) => i !== selectedBox)); setSelectedBox(null); }}
          disabled={selectedBox === null}
        >
          Delete Selected
        </button>
        <button className="btn-clear" onClick={() => { setBoxes([]); setSelectedBox(null); }} disabled={boxes.length === 0}>
          Clear All
        </button>
        <button className="btn-no-otters" onClick={handleNoOtters}>
          No Otters
        </button>
      </div>
    </div>
  );
}