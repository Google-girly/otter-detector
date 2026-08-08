# Auto-labeling & model evaluation

Closes the loop on the otter cam: pull motion clips from S3, cut them into frames,
run the current model to draw boxes, and either build a training set or score the
model for review.

## Prerequisites
- AWS creds configured (`aws sts get-caller-identity` should work) — clips live in `s3://otter-clips/clips/`.
- The repo venv: `../.venv`. Note that the root `requirements.txt` is missing
  some of what this script needs — install `boto3` (S3 listing/download) and,
  for the exporters, `requests` and `Pillow`, on top of it.
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
Open `public-labeling/review.html` in a browser. For each frame you record
**per-frame counts** — how many of the model's boxes were right, and how many
otters it missed (you draw those). The backend collapses those counts into one
readable verdict:

| Verdict | Meaning |
|---|---|
| `correct` | every box on a real otter, nothing missed (True Positive) |
| `missed` | otter present, no box (False Negative) |
| `false_alarm` | box, but no otter (False Positive) |
| `correctly_empty` | no otter, no box (True Negative) |
| `mixed` | more than one of the above on the same frame |

Plus a `box_off` flag for "right otter, sloppy box".

Counting boxes rather than frames is what makes multi-otter frames score
correctly. The page shows live **box-level precision / recall** — the numbers for
writing up model 2.

For comparing two models on a single clip instead, see `clip_review.html` in
[`../public-labeling`](../public-labeling/README.md#clip_reviewhtml--two-models-side-by-side-on-one-clip).

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
| `TRAIN_SPLIT` | `0.8` | train/valid ratio |
| `OUT_DIR` | `dataset` | where the YOLO dataset is written |
| `CLASS_NAME` | `Southern Sea Otter` | the single class name in `data.yaml` |
| `MODEL_VERSION` | model filename stem | recorded on each `eval_predictions` row |

**Supabase creds behave differently in the two modes.** In `--eval` mode the
script explicitly falls back to `../public-labeling/.env`. In auto-labeling mode
it does not — only a `.env` in the current directory is loaded, and there isn't
one in `auto-labeling/`. So a plain run finds no credentials and drops the
uncertain-band frames instead of queueing them (they show up as
`review_no_supabase` in the summary). Export them yourself if you want the review
queue:

```sh
# the .env has CRLF line endings, so strip the \r first — python-dotenv tolerates
# them, but a plain `source` would leave a carriage return on every value
set -a && eval "$(tr -d '\r' < ../public-labeling/.env)" && set +a
../.venv/bin/python pipeline.py
```

## Common flags
- `--since YYYY-MM-DD` — only clips on/after a date (S3 mode)
- `--local ./dir` — process local `.mp4`s instead of S3
- `--limit N` — stop after N clips
- `--dry-run` — detect + report, write/upload nothing
- `--eval` — scoring mode (see above); builds no dataset

## Notes

- **The train/valid split is deterministic.** Frames are hashed by filename
  rather than picked at random, so re-running the pipeline puts a given frame in
  the same split every time instead of leaking it across the boundary.
- **Disk is freed as it goes** — each clip's frames and its temp download are
  deleted as soon as it's processed, so a full-bucket run doesn't need room for
  the whole bucket.
- **Empty frames aren't all discarded.** `NEGATIVE_SAMPLE` (5%) are kept with an
  empty label file. Background samples teach the model what *isn't* an otter and
  measurably cut false alarms.
