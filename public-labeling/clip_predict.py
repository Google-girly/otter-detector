"""
Dump per-frame predictions for one clip, for the side-by-side clip review UI.

Reads the clip with cv2 and predicts on the decoded BGR frames — the SAME code path
clip-test.py uses to burn boxes into the annotated mp4s. That matters: predicting on
ffmpeg-extracted JPEGs instead drops borderline detections (a 0.44-conf box fell under
a 0.4 threshold), so the counts would disagree with the boxes the reviewer can see.

Run from public-labeling/:
  ../.venv/bin/python clip_predict.py ~/Desktop/otter-eval-0724-0907 \
      --clip ~/Desktop/otter-eval-0724-0907/raw.mp4 \
      --model ../models/otter_v1.pt --model ../models/otter_v2.pt --conf 0.4

Writes <dir>/manifest.json consumed by clip_review.html.
"""

import argparse
import json
from pathlib import Path

import cv2
from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser(description="Per-frame model predictions for a clip.")
    ap.add_argument("dir", help="clip eval dir — manifest.json is written here")
    ap.add_argument("--clip", help="the source mp4 (default: <dir>/raw.mp4)")
    ap.add_argument("--model", action="append", default=[], help="path to a .pt (repeatable)")
    ap.add_argument("--conf", type=float, default=0.4,
                    help="confidence threshold — match what made the annotated mp4s (clip-test.py: 0.4)")
    ap.add_argument("--device", default="mps", help="mps on mac, cpu, or 0 for cuda")
    args = ap.parse_args()

    root = Path(args.dir).expanduser()
    clip = Path(args.clip).expanduser() if args.clip else root / "raw.mp4"
    if not clip.exists():
        ap.error(f"no clip at {clip}")
    if not args.model:
        ap.error("give me at least one --model")

    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    manifest = {
        "clip": root.name,
        "conf": args.conf,
        "fps": fps,
        "width": w,
        "height": h,
        "models": [],
    }

    for mp in args.model:
        name = Path(mp).stem
        print(f"\n{name} — conf>={args.conf} device={args.device}")
        model = YOLO(mp)
        per_frame, total, hits, i = [], 0, 0, 0

        cap = cv2.VideoCapture(str(clip))
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            res = model(frame, conf=args.conf, device=args.device, verbose=False)[0]
            boxes = []
            if res.boxes is not None:
                for xyxy, cf in zip(res.boxes.xyxy, res.boxes.conf):
                    x1, y1, x2, y2 = (float(v) for v in xyxy)
                    boxes.append({
                        "x": round(x1), "y": round(y1),
                        "width": round(x2 - x1), "height": round(y2 - y1),
                        "conf": round(float(cf), 3),
                    })
            per_frame.append(boxes)
            total += len(boxes)
            hits += 1 if boxes else 0
            i += 1
            if i % 50 == 0:
                print(f"  {i} frames...")
        cap.release()

        pct = 100 * hits / i if i else 0
        print(f"  {i} frames | {total} boxes | otters on {hits} ({pct:.0f}%)")
        manifest["n_frames"] = i
        manifest["models"].append({"name": name, "path": mp, "boxes": per_frame,
                                   "total_boxes": total, "frames_with_boxes": hits})

    out = root / "manifest.json"
    out.write_text(json.dumps(manifest))
    print(f"\n-> {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
