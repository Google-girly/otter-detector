"""
Run the model over recorded clips and save annotated copies you can scrub through.

Same overlay as yt-test.py (ultralytics' boxes + a coord tag in the box's color),
but reading clips off disk or straight from S3 instead of the live stream, and
writing an H.264 mp4 instead of opening a window — so it works headless.

Run from preliminary-model/:
  # 3 clips from the bucket, newest first, annotated onto the Desktop
  ../.venv/bin/python clip-test.py --s3 3 --out ~/Desktop/otter-annotated

  # specific S3 keys
  ../.venv/bin/python clip-test.py --s3-key clips/2026/07/24/otters-2026-07-24-0907.mp4

  # local files, with the v1 model
  ../.venv/bin/python clip-test.py --model ../models/otter_v1.pt some-clip.mp4
"""

import argparse
import subprocess
from pathlib import Path

import cv2
from ultralytics import YOLO
from ultralytics.utils.plotting import colors  # same palette plot() uses, so tags match the box

BUCKET = "otter-clips"
PREFIX = "clips/"


def list_s3_clips(n):
    """Newest n clip keys in the bucket."""
    out = subprocess.run(
        ["aws", "s3", "ls", f"s3://{BUCKET}/{PREFIX}", "--recursive"],
        capture_output=True, text=True, check=True,
    ).stdout
    keys = [line.split()[3] for line in out.splitlines() if line.strip().endswith(".mp4")]
    return sorted(keys)[-n:]


def fetch_s3(key, dest_dir):
    dest = dest_dir / Path(key).name
    if not dest.exists():
        subprocess.run(
            ["aws", "s3", "cp", f"s3://{BUCKET}/{key}", str(dest), "--no-progress"],
            check=True,
        )
    return dest


def open_writer(path, w, h, fps):
    """ffmpeg encoder fed raw BGR frames — H.264 so QuickTime/Preview can play it."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}",
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)


def annotate(model, src, dst, conf, device):
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        print(f"  SKIP {src.name} — could not open")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    proc = open_writer(dst, w, h, round(fps, 2))

    frames, frames_with_otter, total_boxes = 0, 0, 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        kwargs = {"conf": conf, "verbose": False}
        if device:
            kwargs["device"] = device
        results = model(frame, **kwargs)
        new_frame = results[0].plot()

        # coord tag hanging off each box: filled in the box's own color, white text.
        # shows the top-left corner of the box. ultralytics puts its conf label above
        # the box, so this goes below.
        boxes = results[0].boxes
        n = 0
        if boxes is not None:
            for xyxy, cls in zip(boxes.xyxy, boxes.cls):
                x1, y1, x2, y2 = (int(v) for v in xyxy)
                color = colors(int(cls), True)  # True = BGR, matching the drawn box
                text = f"x:{x1} y:{y1}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                tx = max(0, min(x1, new_frame.shape[1] - tw - 10))          # pull left if it'd clip off the right edge
                ty = y2 if y2 + th + 10 < new_frame.shape[0] else max(0, y2 - th - 10)  # tuck inside if the box hugs the bottom
                cv2.rectangle(new_frame, (tx, ty), (tx + tw + 10, ty + th + 10), color, -1)
                cv2.putText(new_frame, text, (tx + 5, ty + th + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                n += 1

        # frame counter + running detection count, top-left
        hud = f"frame {frames}  |  {n} box(es)"
        cv2.putText(new_frame, hud, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(new_frame, hud, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        proc.stdin.write(new_frame.tobytes())
        frames += 1
        total_boxes += n
        if n:
            frames_with_otter += 1
        if frames % 100 == 0:
            print(f"    {frames} frames...")

    cap.release()
    proc.stdin.close()
    proc.wait()
    return frames, frames_with_otter, total_boxes, fps


def main():
    ap = argparse.ArgumentParser(description="Annotate recorded clips with the model.")
    ap.add_argument("clips", nargs="*", help="local clip paths")
    ap.add_argument("--s3", type=int, metavar="N", help="pull the newest N clips from the bucket")
    ap.add_argument("--s3-key", action="append", default=[], help="specific S3 key (repeatable)")
    ap.add_argument("--model", default="../models/otter_v2.pt")
    ap.add_argument("--conf", type=float, default=0.4, help="confidence threshold (yt-test uses 0.4)")
    ap.add_argument("--device", default="mps", help="mps on mac, cpu, or 0 for cuda")
    ap.add_argument("--out", default="~/Desktop/otter-annotated", help="output folder")
    ap.add_argument("--cache", default="clip-cache", help="where downloaded clips land")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache).expanduser()

    sources = [Path(c).expanduser() for c in args.clips]
    keys = list(args.s3_key)
    if args.s3:
        keys += list_s3_clips(args.s3)
    if keys:
        cache.mkdir(parents=True, exist_ok=True)
        for k in keys:
            print(f"fetching s3://{BUCKET}/{k}")
            sources.append(fetch_s3(k, cache))

    if not sources:
        ap.error("give me some clips: positional paths, --s3 N, or --s3-key")

    model_name = Path(args.model).stem
    print(f"model={model_name} conf>={args.conf} device={args.device} -> {out_dir}")
    model = YOLO(args.model)

    for src in sources:
        dst = out_dir / f"{src.stem}-{model_name}.mp4"
        print(f"\n{src.name}")
        stats = annotate(model, src, dst, args.conf, args.device)
        if not stats:
            continue
        frames, hits, boxes, fps = stats
        pct = 100 * hits / frames if frames else 0
        print(f"  {frames} frames @ {fps:.1f}fps | otters on {hits} ({pct:.0f}%) | {boxes} boxes")
        print(f"  -> {dst}")


if __name__ == "__main__":
    main()
