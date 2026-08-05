# Auto-labeling & model evaluation

Closes the loop on the otter cam: pull motion clips from S3, cut them into frames,
run the current model to draw boxes, and either build a training set or score the
model for review.

## Prerequisites
- AWS creds configured (`aws sts get-caller-identity` should work) — clips live in `s3://otter-clips/clips/`.
- The repo venv: `../.venv` (ultralytics, boto3, opencv, supabase, requests, pillow).
- `ffmpeg` on PATH.

## Two modes

### 1. Evaluation — score the current model (do this first)
Judge how good `otter_v2` actually is before trusting it to label.

**One-time setup**
1. In the Supabase SQL Editor, run `../public-labeling/db/eval_schema.sql` (creates `eval_predictions` + `eval_feedback`).
2. Expose both tables under **Integrations → Data API → Settings**.
3. Supabase creds are read from `../public-labeling/.env` automatically.

**Run it**
```sh
cd auto-labeling
../.venv/bin/python pipeline.py --eval --limit 3      # start small
# ../.venv/bin/python pipeline.py --eval              # all clips
```
This uploads every sampled frame + the model's prediction to `eval_predictions`.
Add `--dry-run` to see the confidence-band split without uploading.

**Review**
```sh
cd ../public-labeling
../.venv/bin/uvicorn main:app --reload
```
Open `public-labeling/review.html` in a browser. For each frame you see the model's
box and click a verdict (keys `1`–`5`):

| Key | Verdict | Meaning |
|---|---|---|
| 1 | Correct | box is on the otter (True Positive) |
| 2 | Missed otter | otter present, no box (False Negative) |
| 3 | False alarm | box, but no otter (False Positive) |
| 4 | Box off | right idea, sloppy box |
| 5 | Correctly empty | no otter, no box (True Negative) |

The page shows live **precision / recall / tight-box rate** — the numbers for writing
up model 2.

### 2. Auto-labeling — build a training set
Once the model is good enough to trust, produce a YOLO dataset. Frames route by
confidence: `≥ AUTO_CONF` auto-accepted into the dataset, `REVIEW_CONF..AUTO_CONF`
pushed to the human review queue, below that treated as empty (5% kept as negatives).

```sh
cd auto-labeling
../.venv/bin/python pipeline.py --dry-run --limit 3   # preview
../.venv/bin/python pipeline.py                        # full run -> dataset/
```

Then fine-tune from the existing model (the command is printed at the end of the run):
```sh
yolo detect train model=../models/otter_v2.pt data=dataset/data.yaml epochs=50 imgsz=640 lr0=0.0001
```

## Config (environment variables)
| Var | Default | Purpose |
|---|---|---|
| `S3_BUCKET` / `S3_PREFIX` | `otter-clips` / `clips` | where clips live |
| `AWS_REGION` | `us-east-2` | |
| `MODEL_PATH` | `../models/otter_v2.pt` | model used to label/score |
| `FRAME_FPS` | `1` | frame extraction rate |
| `AUTO_CONF` | `0.60` | ≥ this → auto-accept / "auto" band |
| `REVIEW_CONF` | `0.25` | ≥ this → review / "review" band |
| `NEGATIVE_SAMPLE` | `0.05` | fraction of empty frames kept as negatives |

## Common flags
- `--since YYYY-MM-DD` — only clips on/after a date (S3 mode)
- `--local ./dir` — process local `.mp4`s instead of S3
- `--limit N` — stop after N clips
- `--dry-run` — detect + report, write/upload nothing
