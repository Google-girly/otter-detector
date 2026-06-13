import { useEffect, useState, useRef } from "react";
import { Stage, Layer, Image, Rect } from "react-konva";
import axios from "axios";
import "./App.css";

const SUPABASE_URL = "https://ofuqvgfdninllnlhtqij.supabase.co";
const BACKEND = "http://127.0.0.1:8000";
const LABELS = ["otter", "not sure"];

export default function App() {
  const [image, setImage] = useState(null);       // Current image record from DB
  const [konvaImage, setKonvaImage] = useState(null); // Loaded image for canvas
  const [boxes, setBoxes] = useState([]);          // Bounding boxes drawn so far
  const [drawing, setDrawing] = useState(false);   // Is the user currently drawing?
  const [currentBox, setCurrentBox] = useState(null); // The box being drawn right now
  // Fetch the next unlabeled image from the backend
  const fetchNextImage = async () => {
    setBoxes([]);
    const res = await axios.get(`${BACKEND}/next-image`);
    setImage(res.data);

    // Load the image from Supabase storage so Konva can display it
    const img = new window.Image();
    img.src = `${SUPABASE_URL}/storage/v1/object/public/frames/${res.data.storage_path}`;
    img.onload = () => setKonvaImage(img);
  };

  useEffect(() => {
    fetchNextImage();
  }, []);

  // Scale pointer position back to original image coordinates
  const getPos = (e) => {
    const pos = e.target.getStage().getPointerPosition();
    const maxW = window.innerWidth - 40;
    const maxH = window.innerHeight - 160;
    const sc = Math.min(maxW / konvaImage.width, maxH / konvaImage.height, 1);
    return { x: pos.x / sc, y: pos.y / sc };
  };

  // When the user clicks down, start drawing a box
  const handleMouseDown = (e) => {
    const pos = getPos(e);
    setDrawing(true);
    setCurrentBox({ x: pos.x, y: pos.y, width: 0, height: 0, label: "otter" });
  };

  // As the user drags, update the box size
  const handleMouseMove = (e) => {
    if (!drawing) return;
    const pos = getPos(e);
    setCurrentBox((prev) => ({
      ...prev,
      width: pos.x - prev.x,
      height: pos.y - prev.y,
    }));
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
    await axios.post(`${BACKEND}/submit-label`, {
      image_id: image.id,
      boxes: boxes,
      labeled_by: "",
    });
    fetchNextImage();
  };

  // Submit with no boxes — marks image as labeled with empty annotation
  const handleNoOtters = async () => {
    await axios.post(`${BACKEND}/submit-label`, {
      image_id: image.id,
      boxes: [],
      labeled_by: "",
    });
    fetchNextImage();
  };

  if (!konvaImage) return <p className="loading">Loading image...</p>;

  // Scale image to fit within the viewport (leaving room for header + buttons)
  const maxW = window.innerWidth - 40;
  const maxH = window.innerHeight - 160;
  const scale = Math.min(maxW / konvaImage.width, maxH / konvaImage.height, 1);
  const stageW = konvaImage.width * scale;
  const stageH = konvaImage.height * scale;

  return (
    <div className="app">
      <div className="header">
        <h1>Otter Labeling Tool</h1>
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
                stroke="#ef4444"
                strokeWidth={2 / scale}
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

      <p className="instructions">Click and drag to draw a bounding box around each otter</p>

      <div className="actions">
        <button className="btn-submit" onClick={handleSubmit} disabled={boxes.length === 0}>
          Submit Labels
        </button>
        <button className="btn-clear" onClick={() => setBoxes([])} disabled={boxes.length === 0}>
          Clear Boxes
        </button>
        <button className="btn-no-otters" onClick={handleNoOtters}>
          No Otters
        </button>
      </div>
    </div>
  );
}