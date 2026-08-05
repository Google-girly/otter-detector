"""
Auto-labeling pipeline: S3 clips -> frames -> model-drawn boxes -> training data.

Closes the loop on the otter cam. The EC2 recorder (../ec2-recorder) writes
motion-triggered clips to S3; this script pulls those clips, cuts them into
frames, runs the existing model (otter_v2.pt) to draw bounding boxes, and routes
each frame by the model's confidence:

    conf >= AUTO_CONF      -> auto-accept: written straight into a YOLO dataset
                              (train/valid split), no human involved.
    REVIEW_CONF..AUTO_CONF -> uncertain: frame + the model's pre-drawn boxes are
                              pushed to the Supabase review queue so a human in the
                              public-labeling UI only has to *correct*, not draw
                              from scratch.
    conf < REVIEW_CONF     -> treated as an empty frame: skipped, except a small
                              NEGATIVE_SAMPLE fraction kept as background negatives
                              (empty label file) which sharpens the model.

Everything is env-driven and every external dependency (S3, Supabase) is
OPTIONAL so you can dry-run locally, exactly like the recorder.

Run from the auto-labeling/ directory:
    python pipeline.py                        # process everything under the S3 prefix
    python pipeline.py --since 2026-07-24
    python pipeline.py --limit 3 --dry-run    # download + detect + report, write nothing
    python pipeline.py --local ./some-clips   # skip S3, use local mp4s
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Config (all overridable via environment variables) ──────────────────────────
S3_BUCKET   = os.environ.get("S3_BUCKET", "otter-clips")        # bucket the recorder uploads to
S3_PREFIX   = os.environ.get("S3_PREFIX", "clips")
AWS_REGION  = os.environ.get("AWS_REGION", "us-east-2")

MODEL_PATH  = os.environ.get("MODEL_PATH", "../models/otter_v2.pt")

# Frame extraction rate. 1 fps matches the public-labeling workflow; motion clips
# are short, so 1 fps yields a handful of well-spaced frames per clip.
FPS         = float(os.environ.get("FRAME_FPS", 1))

# Confidence routing thresholds (see module docstring).
AUTO_CONF       = float(os.environ.get("AUTO_CONF", 0.60))
REVIEW_CONF     = float(os.environ.get("REVIEW_CONF", 0.25))
NEGATIVE_SAMPLE = float(os.environ.get("NEGATIVE_SAMPLE", 0.05))  # keep 5% of empty frames as negatives

TRAIN_SPLIT = float(os.environ.get("TRAIN_SPLIT", 0.8))
OUT_DIR     = Path(os.environ.get("OUT_DIR", "dataset"))
CLASS_NAME  = os.environ.get("CLASS_NAME", "Southern Sea Otter")

# Supabase review queue (optional — leave unset to disable the human-review route).
SUPABASE_URL         = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")   # service role: needed for storage upload
REVIEW_BUCKET        = os.environ.get("REVIEW_BUCKET", "frames")
REVIEW_PREFIX        = os.environ.get("REVIEW_PREFIX", "auto_review")

# --eval mode: push every sampled frame + its prediction to the eval_predictions
# table (see public-labeling/db/eval_schema.sql) for manual model-2 scoring.
# Frames go to the PUBLIC 'frames' bucket so the review page can load them by URL.
EVAL_BUCKET   = os.environ.get("EVAL_BUCKET", "frames")
EVAL_PREFIX   = os.environ.get("EVAL_PREFIX", "eval")
MODEL_VERSION = os.environ.get("MODEL_VERSION", Path(MODEL_PATH).stem)  # e.g. 'otter_v2'


# A deterministic pseudo-random pick for the train/val split and negative sampling.
# We avoid random.random() so a given frame always lands in the same split on
# re-runs (idempotent-ish); hash the frame name instead.
def _bucket(name: str) -> float:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return (h % 10_000) / 10_000.0


# ── S3 ──────────────────────────────────────────────────────────────────────────
def list_s3_clips(since: str | None):
    """Yield (key, s3_client) for every .mp4 under the prefix, optionally filtered
    to keys whose date-partition (clips/YYYY/MM/DD/...) is >= `since` (YYYY-MM-DD)."""
    import boto3

    s3 = boto3.client("s3", region_name=AWS_REGION)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".mp4"):
                continue
            if since and not _key_on_or_after(key, since):
                continue
            yield key, s3


def _key_on_or_after(key: str, since: str) -> bool:
    """clips/2026/07/24/otters-... -> compare 2026-07-24 >= since."""
    parts = key.split("/")
    try:
        yyyy, mm, dd = parts[1], parts[2], parts[3]
        return f"{yyyy}-{mm}-{dd}" >= since
    except IndexError:
        return True  # can't parse a date -> don't filter it out


def download_clip(s3, key: str, dest: Path) -> Path:
    local = dest / os.path.basename(key)
    s3.download_file(S3_BUCKET, key, str(local))
    return local


# ── Frame extraction ──────────────────────────────────────────────────────────────
def extract_frames(clip: Path, frames_dir: Path) -> list[Path]:
    """Cut a clip into JPEG frames at FPS using ffmpeg. Returns the frame paths.
    Frame names are prefixed with the clip stem so they stay globally unique."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    stem = clip.stem
    pattern = str(frames_dir / f"{stem}_%05d.jpg")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(clip),
        "-vf", f"fps={FPS}",
        "-q:v", "2",
        pattern,
    ]
    subprocess.run(cmd, check=True)
    return sorted(frames_dir.glob(f"{stem}_*.jpg"))


# ── YOLO dataset writing ────────────────────────────────────────────────────────
def ensure_dataset_dirs():
    for split in ("train", "valid"):
        (OUT_DIR / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT_DIR / split / "labels").mkdir(parents=True, exist_ok=True)


def write_data_yaml():
    yaml = (
        f"train: {(OUT_DIR / 'train' / 'images').resolve()}\n"
        f"val: {(OUT_DIR / 'valid' / 'images').resolve()}\n\n"
        f"nc: 1\n"
        f"names: ['{CLASS_NAME}']\n"
    )
    (OUT_DIR / "data.yaml").write_text(yaml)


def accept_to_dataset(frame: Path, yolo_lines: list[str]):
    """Copy a frame + its YOLO label into the train or valid split. An empty
    yolo_lines list writes an empty label file (a valid YOLO 'background' sample)."""
    split = "train" if _bucket(frame.name) < TRAIN_SPLIT else "valid"
    (OUT_DIR / split / "images" / frame.name).write_bytes(frame.read_bytes())
    stem = frame.stem
    (OUT_DIR / split / "labels" / f"{stem}.txt").write_text("\n".join(yolo_lines))


# ── Supabase review queue ─────────────────────────────────────────────────────────
_supabase = None


def supabase_client():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase


def push_to_review(frame: Path, pixel_boxes: list[dict]):
    """Upload an uncertain frame to storage and register it as an unlabeled image
    with the model's boxes pre-attached, so a human just corrects them.

    - images row: is_labeled stays FALSE  (so the labeling UI serves it)
    - labels row: labeled_by = 'auto'; the UI reads these boxes as a starting
      point. (Needs the db/ migration + a small UI change to preload existing
      boxes — see auto-labeling/README.md.)
    """
    sb = supabase_client()
    storage_path = f"{REVIEW_PREFIX}/{frame.name}"
    with open(frame, "rb") as f:
        sb.storage.from_(REVIEW_BUCKET).upload(
            storage_path, f, {"content-type": "image/jpeg", "upsert": "true"}
        )
    img = (
        sb.table("images")
        .insert({"filename": frame.name, "storage_path": f"{REVIEW_BUCKET}/{storage_path}"})
        .execute()
    )
    image_id = img.data[0]["id"]
    sb.table("labels").insert({
        "image_id": image_id,
        "labeled_by": "auto",
        "boxes": pixel_boxes,          # UI-friendly pixel coords {x,y,width,height}
        "has_otter": len(pixel_boxes) > 0,
    }).execute()


def push_eval_prediction(frame: Path, bucket_label: str, max_conf: float,
                         boxes_with_conf: list[dict], source_clip: str):
    """Upload a frame to the public bucket and record the model's prediction in
    eval_predictions, for manual scoring in the standalone review page. Covers
    ALL frames (auto/review/empty) so you can measure misses + false alarms."""
    sb = supabase_client()
    storage_path = f"{EVAL_PREFIX}/{frame.name}"
    with open(frame, "rb") as f:
        sb.storage.from_(EVAL_BUCKET).upload(
            storage_path, f, {"content-type": "image/jpeg", "upsert": "true"}
        )
    sb.table("eval_predictions").insert({
        "filename": frame.name,
        "storage_path": f"{EVAL_BUCKET}/{storage_path}",
        "model_version": MODEL_VERSION,
        "source_clip": source_clip,
        "bucket": bucket_label,
        "max_conf": max_conf,
        "boxes": boxes_with_conf,
    }).execute()


def bucket_for(max_conf: float) -> str:
    if max_conf >= AUTO_CONF:
        return "auto"
    if max_conf >= REVIEW_CONF:
        return "review"
    return "empty"


# ── Detection -> routing ───────────────────────────────────────────────────────────
def to_yolo_line(xywhn) -> str:
    """xywhn = normalized (cx, cy, w, h) straight from ultralytics. Class 0."""
    cx, cy, w, h = (float(v) for v in xywhn)
    return f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def to_pixel_box(xyxy) -> dict:
    """xyxy pixel corners -> the {x,y,width,height} shape the labeling UI expects."""
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def to_pixel_box_conf(xyxy, conf) -> dict:
    """xyxy + confidence -> {x,y,width,height,conf} for the eval review page."""
    box = to_pixel_box(xyxy)
    box["conf"] = round(float(conf), 4)
    return box


def process_frames(frames: list[Path], model, stats: dict, dry_run: bool,
                   eval_mode: bool = False, source_clip: str = ""):
    """Run the model on a batch of frames and route each one by confidence."""
    # conf=REVIEW_CONF: ask the model for everything at/above the lowest threshold
    # we care about; we bucket by confidence ourselves below.
    results = model.predict(
        source=[str(f) for f in frames],
        conf=REVIEW_CONF,
        verbose=False,
        stream=True,
    )
    for frame, res in zip(frames, results):
        boxes = res.boxes
        confs = [float(c) for c in boxes.conf] if boxes is not None and len(boxes) else []
        max_conf = max(confs) if confs else 0.0

        # --eval: score-collection mode. Push EVERY frame + prediction to Supabase
        # for manual review; don't build a dataset or touch the review queue.
        if eval_mode:
            label = bucket_for(max_conf)
            stats[label] += 1
            if not dry_run:
                boxes_with_conf = (
                    [to_pixel_box_conf(b, c) for b, c in zip(boxes.xyxy, boxes.conf)]
                    if confs else []
                )
                push_eval_prediction(frame, label, max_conf, boxes_with_conf, source_clip)
            continue

        if max_conf >= AUTO_CONF:
            stats["auto"] += 1
            if not dry_run:
                yolo_lines = [to_yolo_line(b) for b in boxes.xywhn]
                accept_to_dataset(frame, yolo_lines)
        elif max_conf >= REVIEW_CONF:
            stats["review"] += 1
            if not dry_run and SUPABASE_URL:
                pixel_boxes = [to_pixel_box(b) for b in boxes.xyxy]
                push_to_review(frame, pixel_boxes)
            elif not SUPABASE_URL:
                stats["review_no_supabase"] += 1
        else:
            # No confident detection -> treat as empty. Keep a sample as negatives.
            if _bucket("neg-" + frame.name) < NEGATIVE_SAMPLE:
                stats["negative"] += 1
                if not dry_run:
                    accept_to_dataset(frame, [])
            else:
                stats["skipped_empty"] += 1


# ── Main ──────────────────────────────────────────────────────────────────────────
def clip_source(args):
    """Yield (label, local_path, cleanup_path) for each clip to process.
    cleanup_path is the temp file to delete after use (None for --local files)."""
    tmp = Path(tempfile.mkdtemp(prefix="otter-clips-"))
    if args.local:
        for mp4 in sorted(Path(args.local).glob("*.mp4")):
            yield mp4.name, mp4, None
    else:
        if not S3_BUCKET:
            sys.exit("No S3_BUCKET set and no --local path given. Nothing to do.")
        for key, s3 in list_s3_clips(args.since):
            local = download_clip(s3, key, tmp)
            yield key, local, local


def main():
    ap = argparse.ArgumentParser(description="Auto-label otter clips from S3 into a YOLO dataset.")
    ap.add_argument("--since", help="Only process clips dated on/after YYYY-MM-DD (S3 mode).")
    ap.add_argument("--local", help="Process local .mp4 files from this dir instead of S3.")
    ap.add_argument("--dry-run", action="store_true", help="Detect + report, but write nothing.")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N clips (0 = no limit).")
    ap.add_argument("--eval", action="store_true",
                    help="Model-scoring mode: push every frame + prediction to Supabase "
                         "eval_predictions for manual review. Builds no dataset.")
    args = ap.parse_args()

    # Supabase creds live in ../public-labeling/.env; pull them in for --eval.
    if args.eval:
        load_dotenv(Path(__file__).resolve().parent.parent / "public-labeling" / ".env")
        global SUPABASE_URL, SUPABASE_SERVICE_KEY
        SUPABASE_URL = SUPABASE_URL or os.environ.get("SUPABASE_URL")
        SUPABASE_SERVICE_KEY = SUPABASE_SERVICE_KEY or os.environ.get("SUPABASE_SERVICE_KEY")
        if not args.dry_run and not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
            sys.exit("--eval needs SUPABASE_URL + SUPABASE_SERVICE_KEY "
                     "(checked env and ../public-labeling/.env).")

    from ultralytics import YOLO

    src = f"local:{args.local}" if args.local else f"s3://{S3_BUCKET}/{S3_PREFIX}"
    print(f"[boot] model={MODEL_PATH}  auto>={AUTO_CONF}  review>={REVIEW_CONF}  fps={FPS}")
    print(f"[boot] source={src}")
    if args.eval:
        print(f"[boot] EVAL mode -> Supabase eval_predictions (model_version={MODEL_VERSION})"
              + ("  (DRY RUN — nothing uploaded)" if args.dry_run else ""))
    else:
        print(f"[boot] dataset out -> {OUT_DIR.resolve()}"
              + ("  (DRY RUN — nothing written)" if args.dry_run else ""))
    if not args.dry_run and not args.eval:
        ensure_dataset_dirs()
    model = YOLO(MODEL_PATH)

    stats = {k: 0 for k in
             ("clips", "frames", "auto", "review", "empty", "negative",
              "skipped_empty", "review_no_supabase")}
    frames_root = Path(tempfile.mkdtemp(prefix="otter-frames-"))

    for i, (label, clip, cleanup) in enumerate(clip_source(args)):
        if args.limit and i >= args.limit:
            break
        stats["clips"] += 1
        frames_dir = frames_root / Path(clip).stem
        try:
            frames = extract_frames(Path(clip), frames_dir)
        except subprocess.CalledProcessError:
            print(f"[warn] ffmpeg failed on {label} — skipping", flush=True)
            continue
        stats["frames"] += len(frames)
        print(f"[clip {stats['clips']}] {label}: {len(frames)} frames", flush=True)
        process_frames(frames, model, stats, args.dry_run,
                       eval_mode=args.eval, source_clip=label)
        # free disk as we go
        for f in frames:
            f.unlink(missing_ok=True)
        if cleanup:
            Path(cleanup).unlink(missing_ok=True)

    if not args.dry_run and not args.eval and (stats["auto"] or stats["negative"]):
        write_data_yaml()

    print("\n── Summary ─────────────────────────────")
    print(json.dumps(stats, indent=2))
    total = stats["frames"] or 1
    if args.eval:
        print(f"auto: {stats['auto']} ({100*stats['auto']/total:.0f}%)  "
              f"review: {stats['review']} ({100*stats['review']/total:.0f}%)  "
              f"empty: {stats['empty']} ({100*stats['empty']/total:.0f}%)")
        if not args.dry_run:
            print("\nUploaded to eval_predictions. Score them in the review page:")
            print("  cd ../public-labeling && uvicorn main:app --reload")
            print("  then open review.html")
    else:
        print(f"auto-accept: {stats['auto']} ({100*stats['auto']/total:.0f}%)  "
              f"review: {stats['review']} ({100*stats['review']/total:.0f}%)  "
              f"empty: {stats['skipped_empty'] + stats['negative']} "
              f"({100*(stats['skipped_empty']+stats['negative'])/total:.0f}%)")
        if not args.dry_run:
            print(f"\nDataset: {OUT_DIR.resolve()}")
            print("Fine-tune from the existing model with:")
            print(f"  yolo detect train model={MODEL_PATH} "
                  f"data={(OUT_DIR / 'data.yaml').resolve()} epochs=50 imgsz=640 lr0=0.0001")


if __name__ == "__main__":
    main()
