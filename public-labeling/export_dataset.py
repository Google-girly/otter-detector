"""
Pulls labeled images and bounding boxes from Supabase and writes them
to a YOLO-format dataset ready for fine-tuning.

Output structure:
  dataset/
    train/images/  train/labels/
    valid/images/  valid/labels/
    data.yaml

Run from the public-labeling/ directory:
  python export_dataset.py
"""

import io
import os
import random
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TRAIN_SPLIT = 0.8
OUT_DIR = Path("dataset")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- fetch labeled images + their boxes ---
print("Fetching labeled images from Supabase...")
resp = (
    supabase.table("images")
    .select("id, filename, storage_path, labels(boxes, has_otter)")
    .eq("is_labeled", True)
    .execute()
)
rows = resp.data
print(f"  {len(rows)} labeled images found")

if not rows:
    print("Nothing to export. Label some images first!")
    raise SystemExit(0)

# --- 80/20 train/val split ---
random.shuffle(rows)
cut = int(len(rows) * TRAIN_SPLIT)
splits = {"train": rows[:cut], "valid": rows[cut:]}

for split in splits:
    (OUT_DIR / split / "images").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / split / "labels").mkdir(parents=True, exist_ok=True)

# --- download images + write YOLO label files ---
def to_yolo(box, img_w, img_h):
    """Convert pixel-coord box to YOLO normalized format."""
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    # handle boxes drawn right-to-left or bottom-to-top
    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h
    cx = (x + w / 2) / img_w
    cy = (y + h / 2) / img_h
    nw = w / img_w
    nh = h / img_h
    return f"0 {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}"

total = sum(len(v) for v in splits.values())
done = 0

for split_name, images in splits.items():
    for row in images:
        storage_path = row["storage_path"]
        # derive filename from storage_path — some rows have empty filename from the populate SQL
        filename = os.path.basename(storage_path) or row["filename"]
        if not filename:
            continue
        boxes = row["labels"][0]["boxes"] if row.get("labels") else []

        # download
        url = f"{SUPABASE_URL}/storage/v1/object/public/frames/{storage_path}"
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  SKIP {filename} — download failed ({r.status_code})")
            continue

        img_bytes = r.content
        img_w, img_h = Image.open(io.BytesIO(img_bytes)).size

        # save image
        (OUT_DIR / split_name / "images" / filename).write_bytes(img_bytes)

        # save label file (empty = no otters, which is valid YOLO)
        stem = Path(filename).stem
        label_lines = [to_yolo(b, img_w, img_h) for b in boxes]
        (OUT_DIR / split_name / "labels" / f"{stem}.txt").write_text(
            "\n".join(label_lines)
        )

        done += 1
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total}")

# --- write data.yaml ---
yaml = f"""\
train: {(OUT_DIR / 'train' / 'images').resolve()}
val: {(OUT_DIR / 'valid' / 'images').resolve()}

nc: 1
names: ['Southern Sea Otter']
"""
(OUT_DIR / "data.yaml").write_text(yaml)

print(f"\nDone! {splits['train'].__len__()} train / {splits['valid'].__len__()} val")
print(f"Dataset saved to: {OUT_DIR.resolve()}")
print(f"\nTo fine-tune your existing model, run from public-labeling/:")
print(f"  yolo detect train model=../models/otter_v1.pt data={OUT_DIR.resolve() / 'data.yaml'} epochs=50 imgsz=640 lr0=0.0001")
