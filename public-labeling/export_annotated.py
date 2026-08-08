"""
Export annotated eval frames.

Pulls every frame in eval_predictions plus whatever verdict/corrections you gave
it in eval_feedback, then draws them onto the image and saves a local copy —
grouped into a folder per classification and color-coded by verdict. Useful for
scrolling through the model's behavior or dropping figures into the model-2 writeup.

On each frame:
  • model's predicted boxes  — drawn in the VERDICT color (so the color tells you
    how you judged the frame), labeled with confidence + pixel coords
    ("89% 412,233 88x64" = top-left x,y then width x height). --no-coords drops
    the coords.
  • your drawn correction boxes (missed otters) — solid CYAN, labeled "you" + coords.
  • a header strip with the verdict + counts (TP/FP/FN) + filename.

Output:
  annotated/
    correct/ missed/ false_alarm/ box_off/ correctly_empty/ mixed/ unreviewed/

Run from public-labeling/:
  ../.venv/bin/python export_annotated.py
"""

import argparse
import io
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
OUT_DIR = Path(os.getenv("ANNOTATED_DIR", "annotated"))

# One color per classification you can assign. Model boxes take this color so the
# box color itself tells you the verdict at a glance.
VERDICT_COLOR = {
    "correct":         (53, 224, 127),   # green
    "missed":          (255, 180, 84),   # orange
    "false_alarm":     (255, 77, 77),    # red
    "box_off":         (58, 160, 255),   # blue
    "correctly_empty": (150, 165, 178),  # gray
    "mixed":           (198, 120, 255),  # purple
    "unreviewed":      (235, 235, 235),  # near-white
}
CORRECTION_COLOR = (0, 224, 224)         # cyan — always the human-drawn boxes

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _font(size):
    try:
        return ImageFont.truetype("Arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _coord_text(b):
    """x,y of the top-left corner plus w×h, rounded to whole pixels."""
    return (f"{round(b['x'])},{round(b['y'])} "
            f"{round(b['width'])}x{round(b['height'])}")


def _draw_box(draw, b, color, width, label, font, img_w, coords=True):
    x, y, w, h = b["x"], b["y"], b["width"], b["height"]
    draw.rectangle([x, y, x + w, y + h], outline=color, width=width)
    if coords:
        label = f"{label} {_coord_text(b)}".strip()
    if label:
        tw = draw.textlength(label, font=font)
        # keep the label strip inside the frame — coords make it wide enough to
        # run off the right edge / above the top on boxes near a border
        lx = max(0, min(x, img_w - tw - 7))
        ly = y if y >= 16 else y + h + 16
        draw.rectangle([lx, ly - 16, lx + tw + 6, ly], fill=color)
        draw.text((lx + 3, ly - 15), label, fill=(10, 20, 28), font=font)


def main():
    ap = argparse.ArgumentParser(description="Export annotated eval frames.")
    ap.add_argument("--no-coords", dest="coords", action="store_false",
                    help="omit the x,y w x h text from box labels")
    args = ap.parse_args()

    preds = supabase.table("eval_predictions").select("*").limit(10000).execute().data
    fb_rows = supabase.table("eval_feedback").select("*").limit(10000).execute().data
    fb = {r["prediction_id"]: r for r in fb_rows}
    print(f"{len(preds)} predictions, {len(fb)} reviewed")

    font = _font(13)
    hfont = _font(15)
    counts = {}
    done = 0

    for p in preds:
        review = fb.get(p["id"])
        verdict = review["verdict"] if review else "unreviewed"
        color = VERDICT_COLOR.get(verdict, VERDICT_COLOR["unreviewed"])

        # download the raw frame from the public bucket
        url = f"{SUPABASE_URL}/storage/v1/object/public/{p['storage_path']}"
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  SKIP {p['filename']} — download failed ({r.status_code})")
            continue
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        draw = ImageDraw.Draw(img)

        # model's predicted boxes, in the verdict color
        for b in (p.get("boxes") or []):
            conf = b.get("conf")
            _draw_box(draw, b, color, 2,
                      f"{conf*100:.0f}%" if conf is not None else "",
                      font, img.width, args.coords)

        # your correction boxes (missed otters), in cyan
        if review:
            for b in (review.get("corrected_boxes") or []):
                _draw_box(draw, b, CORRECTION_COLOR, 3, "you", font, img.width, args.coords)

        # header strip: verdict + counts + filename
        if review:
            hdr = (f"{verdict.upper()}   TP:{review.get('n_correct',0)} "
                   f"FP:{review.get('n_false',0)} FN:{review.get('n_missed',0)}"
                   f"{'  box-off' if review.get('box_off') else ''}   {p['filename']}")
        else:
            hdr = f"UNREVIEWED   {len(p.get('boxes') or [])} model box(es)   {p['filename']}"
        draw.rectangle([0, 0, img.width, 20], fill=color)
        draw.text((5, 3), hdr, fill=(10, 20, 28), font=hfont)

        out = OUT_DIR / verdict
        out.mkdir(parents=True, exist_ok=True)
        img.save(out / p["filename"], quality=90)
        counts[verdict] = counts.get(verdict, 0) + 1
        done += 1
        if done % 20 == 0:
            print(f"  {done}/{len(preds)}")

    print(f"\nDone — {done} frames written to {OUT_DIR.resolve()}")
    for v, n in sorted(counts.items()):
        print(f"  {v}: {n}")


if __name__ == "__main__":
    main()
