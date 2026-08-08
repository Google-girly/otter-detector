"""
Annotate the eval frames with ANY model, for model-to-model comparison.

Pulls the SAME sample frames used in the v2 review (the clean originals from
Supabase Storage — no boxes baked in), runs the model you point it at, and draws
that model's boxes onto each frame. Output is grouped by the v2 verdict so you
can line frames up folder-for-folder against public-labeling/annotated/ (v2).

Run from public-labeling/:
  ../.venv/bin/python annotate_model.py                              # v1 vs the same frames
  ../.venv/bin/python annotate_model.py --model ../models/otter_v2.pt --out annotated_v2_check
  ../.venv/bin/python annotate_model.py --model ../runs/.../best.pt --out annotated_v3
"""

import argparse
import io
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from supabase import create_client
from ultralytics import YOLO

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

BOX_COLOR = (255, 77, 77)   # this model's boxes (red)
HDR_COLOR = (58, 160, 255)  # header strip (blue)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _font(size):
    try:
        return ImageFont.truetype("Arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser(description="Annotate eval frames with a given model.")
    ap.add_argument("--model", default="../models/otter_v1.pt", help="path to the .pt model")
    ap.add_argument("--out", default="annotated_v1", help="output folder")
    ap.add_argument("--conf", type=float, default=0.25,
                    help="confidence threshold (match the v2 eval's 0.25 for a fair compare)")
    ap.add_argument("--no-coords", dest="coords", action="store_false",
                    help="omit the x,y w x h text from box labels")
    args = ap.parse_args()

    model_name = Path(args.model).stem
    out_dir = Path(args.out)
    font, hfont = _font(13), _font(15)

    preds = supabase.table("eval_predictions").select("id, filename, storage_path").limit(10000).execute().data
    fb_rows = supabase.table("eval_feedback").select("prediction_id, verdict").limit(10000).execute().data
    verdict_of = {r["prediction_id"]: r["verdict"] for r in fb_rows}
    print(f"{len(preds)} frames | model={model_name} | conf>={args.conf}")

    model = YOLO(args.model)
    done, total_boxes = 0, 0

    for p in preds:
        v2_verdict = verdict_of.get(p["id"], "unreviewed")
        url = f"{SUPABASE_URL}/storage/v1/object/public/{p['storage_path']}"
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  SKIP {p['filename']} — download failed ({r.status_code})")
            continue

        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        res = model.predict(img, conf=args.conf, verbose=False)[0]

        draw = ImageDraw.Draw(img)
        n = 0
        if res.boxes is not None:
            for xyxy, conf in zip(res.boxes.xyxy, res.boxes.conf):
                x1, y1, x2, y2 = (float(v) for v in xyxy)
                draw.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=2)
                label = f"{float(conf)*100:.0f}%"
                if args.coords:
                    label += f" {round(x1)},{round(y1)} {round(x2-x1)}x{round(y2-y1)}"
                tw = draw.textlength(label, font=font)
                # keep the label strip inside the frame — coords make it wide
                # enough to run off the right edge / above the top
                lx = max(0, min(x1, img.width - tw - 7))
                ly = y1 if y1 >= 16 else y2 + 16
                draw.rectangle([lx, ly - 16, lx + tw + 6, ly], fill=BOX_COLOR)
                draw.text((lx + 3, ly - 15), label, fill=(10, 20, 28), font=font)
                n += 1
        total_boxes += n

        hdr = f"{model_name.upper()}   {n} box(es)   [v2 said: {v2_verdict}]   {p['filename']}"
        draw.rectangle([0, 0, img.width, 20], fill=HDR_COLOR)
        draw.text((5, 3), hdr, fill=(10, 20, 28), font=hfont)

        dest = out_dir / v2_verdict
        dest.mkdir(parents=True, exist_ok=True)
        img.save(dest / p["filename"], quality=90)
        done += 1
        if done % 20 == 0:
            print(f"  {done}/{len(preds)}")

    print(f"\nDone — {done} frames, {total_boxes} total {model_name} boxes -> {out_dir.resolve()}")
    print(f"Compare against v2 in: {Path('annotated').resolve()}")


if __name__ == "__main__":
    main()
