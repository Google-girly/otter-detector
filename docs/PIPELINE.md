# How the otter detector works

The process, the features, and the reasoning behind both. Start here if you're
picking this repo up cold; the per-directory READMEs are the operating manuals
for each stage.

---

## The problem this shape solves

Fine-tuning a detector on 347 Roboflow images gets you a model that works on
images that look like those 347. The otter cam does not cooperate: otters float
belly-up, dive, hide behind kelp, sit half-submerged, and the water ripples
constantly. Getting better means feeding the model *more of the footage it will
actually see* — which means recording that footage, getting it labeled, and
knowing whether the new model is genuinely better than the old one.

Doing that by hand doesn't scale. A person watching a 12-hour stream to find the
interesting minutes, then drawing boxes on 43,000 frames, is not a plan. So the
repo is built around three moves that each remove a chunk of that work:

1. **Only keep footage where something happened.** Motion detection, not a model
   — cheap enough to run 12 hours a day on a $15/month instance.
2. **Let the model do the first draft of the labeling.** Frames it's confident
   about go straight into the training set; only the uncertain ones reach a
   human, and even those arrive with boxes already drawn.
3. **Measure before trusting.** A model that auto-labels its own training data
   can happily reinforce its own blind spots. So evaluation is a separate,
   deliberately human step, on separate tables, before any auto-labeling run.

---

## The loop

```
        ┌─────────────────────────────────────────────────────────┐
        │              YouTube otter cam (7am–7pm PT)             │
        └────────────────────────┬────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      ec2-recorder/      │   MOG2 motion detection
                    │   motion_recorder.py    │   pre-roll + tail
                    └────────────┬────────────┘
                                 │  one H.264 clip per motion event
                    ┌────────────▼────────────┐
                    │  S3  clips/YYYY/MM/DD/  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │     auto-labeling/      │   ffmpeg → 1 fps frames
                    │       pipeline.py       │   model → boxes
                    └──┬────────┬─────────┬───┘   route by confidence
                       │        │         │
           conf ≥ 0.60 │        │ 0.25–.60│  < 0.25
                       │        │         │
              ┌────────▼──┐  ┌──▼─────────▼──────────┐
              │   YOLO    │  │   public-labeling/    │
              │  dataset  │  │  humans draw + judge  │
              └────────┬──┘  └──────────┬────────────┘
                       │                │
                       └────► train ◄───┘
                                │
                       models/otter_vN.pt
```

Each stage is independently runnable. The recorder works without S3, the pipeline
works without Supabase, and both — plus `clip-test.py` — have a dry-run or local
mode. Nothing requires the full stack to be up before you can test one piece.
(The Supabase exporters are the exception: they need a real project and real
rows.)

---

## Stage 1 — Recording (`ec2-recorder/`)

**What it does:** watches the live stream continuously and saves one video clip
per motion event to S3.

**How.** `ffmpeg` pulls the stream and pipes raw BGR frames to Python. An OpenCV
MOG2 background subtractor flags frames where enough pixels changed. A small
state machine turns that into clips:

- **Idle** — frames go into a rolling pre-roll buffer (3 s by default).
- **Motion trips** — an ffmpeg encoder starts, the pre-roll is flushed into it
  first, so the clip doesn't begin mid-action.
- **Recording** — continues until the frame has been quiet for `TAIL_SECONDS`
  (15 s) or `MAX_CLIP_SECONDS` (10 min) is hit.
- **Stop** — the encoder is closed, the finished clip uploads on a background
  thread, and the local copy is deleted.

Notable choices:

- **Motion, not YOLO.** Running the detector on every frame of a 12-hour stream
  would need a GPU instance. Background subtraction runs on a half-resolution
  frame, on 2 CPU cores, alongside the decode and encode. (The pixel count is
  scaled back up by 4 before the threshold comparison, so `MOTION_AREA_FRAC`
  means the same thing regardless of the downscale.)
- **`detectShadows=False` and a 1.2% area threshold.** Water is the enemy on a
  water cam. MOG2 learns constant rippling as background within a few seconds
  (`WARMUP_FRAMES`), and the area threshold ignores what's left. If you still
  get ripple clips, raise `MOTION_AREA_FRAC` before touching anything else.
- **A 15-second tail.** An otter that pauses for four seconds shouldn't produce
  two clips. Longer tail = fewer, longer, more useful clips.
- **H.264 via a second ffmpeg process**, not OpenCV's built-in `mp4v` writer —
  clearer, smaller, and playable in browsers and QuickTime (`yuv420p`).
- **Expiring YouTube URLs are assumed, not avoided.** They expire after minutes.
  When ffmpeg dies the script re-resolves the URL, restarts, resets the
  background model, and discards any half-written clip.
- **Uploads never block recording.** Each finished clip goes to its own thread,
  and an upload failure is logged rather than raised — a bad minute on S3 must
  not take the recorder down.
- **The instance is the schedule.** The recorder is a systemd service set to
  `enable`, so it starts when the machine powers on. EventBridge Scheduler starts
  the instance at 6:55am Pacific and stops it at 7:05pm — matching stream hours,
  halving the bill, and giving the burstable `t3.small` twelve hours a night to
  recharge CPU credits.

Clips are named `otters-YYYY-MM-DD-HHMM.mp4` in Pacific time and land in
`clips/YYYY/MM/DD/` matching that name, with `-2`, `-3` suffixes for same-minute
collisions.

**Gotcha worth knowing:** YouTube challenges datacenter IPs with a bot gate.
Running on EC2 requires `COOKIES_FILE` pointing at a `cookies.txt` exported from
a logged-in browser. From home you don't need it.

---

## Stage 2 — Frames and routing (`auto-labeling/pipeline.py`)

**What it does:** turns clips into either training data or human work.

Per clip: download from S3 → `ffmpeg` at 1 fps → run the model on the batch →
route each frame by its highest box confidence.

| Band | Threshold | Where the frame goes |
|---|---|---|
| **auto** | `conf ≥ 0.60` | Straight into the YOLO dataset with the model's boxes. No human. |
| **review** | `0.25 ≤ conf < 0.60` | Supabase review queue, with the model's boxes pre-attached — the human *corrects* rather than draws. |
| **empty** | `conf < 0.25` | Discarded, except a 5% sample kept as background negatives. |

Why these three bands:

- The **auto** band is where the leverage is. If the model is right 9 times in 10
  above 0.60, auto-accepting is nearly free labeled data.
- The **review** band is exactly the frames worth a person's attention: the model
  saw *something* but isn't sure. Pre-drawn boxes turn a drawing task into a
  yes/no/nudge task.
- The **empty** band isn't thrown away entirely. `NEGATIVE_SAMPLE` keeps 5% with
  an empty label file. Empty labels are valid YOLO samples, and they teach the
  model what a kelp shadow isn't — which is how you cut false alarms.

Two more details that matter more than they look:

- **The train/valid split is deterministic**, hashed from the filename rather
  than randomized. Re-running the pipeline puts a frame in the same split every
  time, so re-runs can't leak a validation frame into training.
- **Disk is freed as it goes.** Frames and downloaded clips are unlinked
  immediately after processing, so a whole-bucket run doesn't need whole-bucket
  disk.

`--eval` switches the whole thing into scoring mode: every sampled frame and its
prediction is pushed to `eval_predictions` for human judging, and no dataset is
written. That's stage 3.

---

## Stage 3 — Humans (`public-labeling/`)

Two jobs live here that look similar and are not:

|  | Labeling | Evaluation |
|---|---|---|
| Question | "Where are the otters?" | "Was the model right?" |
| Produces | Ground truth → training data | Precision / recall |
| Tables | `images`, `labels` | `eval_predictions`, `eval_feedback` |
| UI | React app (`labeling-ui/`) | `review.html`, `clip_review.html` |

**They never share a table on purpose.** Evaluation records what a model
predicted. If those rows fed the training set, the next model would be trained on
its predecessor's guesses and would inherit its blind spots with extra
confidence. The separation is the guardrail.

### 3a. Labeling — building ground truth

A React + Konva canvas serves an unlabeled frame, the labeler drags boxes, and
`POST /api/submit-label` saves them and marks the frame done. `/api/next-image`
picks randomly from a batch of 50 unlabeled rows rather than always returning the
oldest, so two people labeling at once rarely land on the same frame. Touch
support means it works on a phone.

An admin view behind `ADMIN_PASSWORD` shows completion stats and lets you correct
anyone's boxes after the fact.

`export_dataset.py` turns the result into a YOLO dataset: downloads the
originals, 80/20 split, pixel boxes → normalized YOLO lines (normalizing boxes
that were drawn right-to-left or bottom-to-top along the way), empty label files
for otter-free frames, and a `data.yaml`.

### 3b. Evaluation — scoring a model

Before a model is allowed to auto-label, a human scores it.

**`review.html`** walks one model's frames one at a time. The key design decision
is that the reviewer records **counts, not a category**: how many of the model's
boxes were correct, and how many otters it missed (drawn in). The verdict —
`correct` / `missed` / `false_alarm` / `correctly_empty` / `mixed` — is *derived*
server-side, so the arithmetic lives in one place and a frame with two otters and
one box doesn't have to be forced into a single label.

That's also why the scoreboard is **box-level**, not frame-level: TP, FP, and FN
sum over boxes across all frames, so a frame where the model found one otter and
missed another contributes honestly to both precision and recall.

The human's correction boxes are kept in `corrected_boxes`. Those are the highest
value training examples in the whole system — they are, by definition, exactly
what the current model gets wrong.

**`clip_review.html`** answers a different question: *which of two models is
better on this footage?* It shows one clip frame by frame with both models' boxes
side by side, and it's completely offline — a `manifest.json`, three folders of
JPEGs, scores in `localStorage`, export to JSON and CSV. No backend, no database,
nothing to deploy. Press `x` to hide the boxes when you need to decide what's
actually in the frame without the model's opinion leading you.

It defaults to scoring **every 5th frame**. At the recorder's 10–15 fps, adjacent
frames are nearly identical; scoring all of them multiplies the work without
adding information.

### 3c. Exporters — looking at what happened

- **`export_annotated.py`** — every eval frame with v2's boxes drawn *in the
  verdict's color* (green correct, orange missed, red false alarm, blue box-off,
  gray correctly-empty, purple mixed), your cyan corrections on top, and a header
  strip with the counts. Grouped `annotated/<verdict>/`. Scroll a folder and you
  see a failure mode.
- **`annotate_model.py`** — the same clean frames run through *any* model, and
  grouped by **v2's** verdict, so `annotated_v1/missed/` and `annotated/missed/`
  hold the same frames. Folder-for-folder comparison. Match the confidence
  thresholds or the comparison is meaningless.

---

## Stage 4 — Training and promotion

```bash
yolo detect train model=../models/otter_v2.pt data=dataset/data.yaml \
     epochs=50 imgsz=640 lr0=0.0001
```

Fine-tune *from the current model*, not from scratch — a low `lr0` adapts it to
new footage without discarding what it already knows. Both `pipeline.py` and
`export_dataset.py` print this command at the end of a run with their own paths
filled in — though `export_dataset.py` still hardcodes `otter_v1.pt` as the
starting model, so change that to whatever you're actually fine-tuning from.

Ultralytics writes to `runs/detect/train*/weights/best.pt`. Promote a keeper by
copying it to `models/otter_vN.pt`; every script in the repo loads from `models/`
by name, so promotion is the only step that makes a new model "real."

---

## Feature reference

### Recording
| File | What it is |
|---|---|
| `ec2-recorder/motion_recorder.py` | The recorder. Motion state machine, H.264 encoding, threaded S3 upload, stream-reconnect handling. |
| `ec2-recorder/otter-recorder.service` | systemd unit — auto-start on boot, auto-restart on crash. |

### Pipeline
| File | What it is |
|---|---|
| `auto-labeling/pipeline.py` | S3 → frames → boxes → routing. `--eval` for scoring mode, `--local`, `--since`, `--limit`, `--dry-run`. |

### Human-facing
| File | What it is |
|---|---|
| `public-labeling/main.py` | FastAPI: labeling routes, admin routes, eval routes. |
| `public-labeling/labeling-ui/` | React + Vite + react-konva crowd labeling app, with an admin editor. |
| `public-labeling/review.html` | Single-model eval page. Per-frame counts, live box-level precision/recall. |
| `public-labeling/clip_review.html` | Two-model side-by-side clip scrubber. Offline, `localStorage`, JSON/CSV export. |
| `public-labeling/clip_predict.py` | Builds the `manifest.json` that `clip_review.html` reads. |
| `public-labeling/export_annotated.py` | v2's boxes + human corrections, color-coded by verdict. |
| `public-labeling/annotate_model.py` | Any model's boxes on the same frames, grouped by v2's verdict. |
| `public-labeling/export_dataset.py` | Labeled Supabase rows → YOLO dataset. |
| `public-labeling/upload_frames.py` | Resumable bulk upload to Supabase Storage. |
| `public-labeling/upload_to_db.py` | Register uploaded frames in `images`. |

### Model work
| File | What it is |
|---|---|
| `preliminary-model/train.py` · `train-mac.py` | The original fine-tune (`train-mac` adds `device="mps"`). |
| `preliminary-model/yt-test.py` · `yt-test2.py` | Live stream through v1 / v2. Near-identical so you can run both at once. |
| `preliminary-model/clip-test.py` | Model over recorded clips → annotated H.264 mp4s. Headless. |
| `preliminary-model/stream-test.py` | Stream viewer, no model. Isolates stream problems from model problems. |
| `preliminary-model/yolo-test.py` | Base model on a single image. |

---

## Data model

### S3
```
s3://otter-clips/clips/YYYY/MM/DD/otters-YYYY-MM-DD-HHMM.mp4
```
Date-partitioned in Pacific time, derived from the filename so key and name
always agree. `--since YYYY-MM-DD` filters on the partition without listing
object metadata.

### Supabase — labeling
| Table | Columns that matter |
|---|---|
| `images` | `filename`, `storage_path`, `is_labeled` |
| `labels` | `image_id`, `boxes` (JSONB, pixel `{x,y,width,height}`), `labeled_by`, `has_otter` |

`labeled_by` distinguishes a person's email from `"auto"` (pipeline pre-fill) and
`"admin"` (a correction).

### Supabase — evaluation
| Table | Columns that matter |
|---|---|
| `eval_predictions` | `storage_path`, `model_version`, `source_clip`, `bucket` (`auto`/`review`/`empty`), `max_conf`, `boxes` (with `conf`) |
| `eval_feedback` | `prediction_id`, `verdict`, `n_boxes`, `n_correct`, `n_false`, `n_missed`, `box_off`, `corrected_boxes` |

A prediction with no feedback row *is* the definition of "unreviewed" — that's
what `/api/eval/next` serves. No status column to keep in sync.

`db/eval_schema.sql` is **partly** idempotent: the later migrations
(`corrected_boxes`, the count columns) use `ADD COLUMN IF NOT EXISTS`, but the
`CREATE TABLE` / `CREATE INDEX` statements at the top are bare. Re-running the
whole file on an existing database fails with *relation already exists* — run it
once, and apply only the trailing migration block afterwards.

### YOLO dataset
```
dataset/
├── train/images/  train/labels/
├── valid/images/  valid/labels/
└── data.yaml            # nc: 1, names: ['Southern Sea Otter']
```
One class. `<stem>.txt` per image, `0 cx cy w h` normalized. An empty label file
is a deliberate background negative, not a bug.

---

## Where the models stand

| Model | Behavior |
|---|---|
| `otter_v1.pt` | Aggressive. Higher recall, more false alarms. |
| `otter_v2.pt` | Conservative. Higher precision, misses roughly half the otters — mostly ones in the water. |

Measured as of **2026-07-28**, v2 scored **precision 0.89, recall 0.51** — on 90
frames from only **3 clips**. On a separate 272-frame clip, v1 drew 388 boxes
across 248 frames where v2 drew 174 across 147.

**Read those numbers with the caveat attached.** Three clips is not a
distribution: three clips share lighting, tide, camera angle, and probably the
same individual otters. The bottleneck on the next model is clip *diversity*, not
frame count. They're also different operating points on the same tradeoff, not a
straight upgrade — "v2 is better" isn't established by these numbers.

### Confidence thresholds are not the same everywhere

| Context | Threshold | Why |
|---|---|---|
| Pipeline routing | `0.25` / `0.60` | The two band edges. |
| `review.html` eval | `0.25` | Sees everything the pipeline would ever route, including weak detections. |
| Live scripts, `clip-test.py`, `clip_predict.py` | `0.40` | Watchable — high enough that the display isn't noise. |

**When comparing two models, match the threshold.** A 0.25-threshold run against
a 0.40-threshold run measures the thresholds, not the models. This is why
`clip_predict.py` decodes video with cv2 rather than reading extracted JPEGs: it
has to reproduce `clip-test.py`'s exact code path, since re-encoding shifts
borderline boxes across the line (one real case: a 0.44-conf box fell under 0.4),
and then the numbers on screen would contradict the boxes the reviewer sees.

---

## Running a full cycle

```bash
# 1. Clips accumulate on their own (EC2, 7am–7pm PT). Check:
aws s3 ls s3://otter-clips/clips/ --recursive | tail

# 2. Score the current model before trusting it
cd auto-labeling
../.venv/bin/python pipeline.py --eval --limit 3     # start small
cd ../public-labeling && ../.venv/bin/uvicorn main:app --reload
#    open review.html, score frames, watch precision/recall settle

# 3. Look at the failures
../.venv/bin/python export_annotated.py              # -> annotated/<verdict>/
../.venv/bin/python annotate_model.py --model ../models/otter_v1.pt --out annotated_v1

# 4. Good enough? Build a dataset
cd ../auto-labeling
../.venv/bin/python pipeline.py --dry-run --limit 3  # preview the band split
../.venv/bin/python pipeline.py                      # full run -> dataset/

# 5. Fine-tune, then promote
yolo detect train model=../models/otter_v2.pt data=dataset/data.yaml \
     epochs=50 imgsz=640 lr0=0.0001
cp ../runs/detect/train*/weights/best.pt ../models/otter_v3.pt

# 6. Did it help? Two models, one clip, side by side
#    (see public-labeling/README.md for building the clip eval dir)
```

Every step has a small-scale rehearsal — `--limit`, `--dry-run`, `--local`. Use
them. A full-bucket run with a bad threshold is a lot of wasted S3 reads and a
polluted review queue.

---

## Known gaps

Honest list of what isn't built yet:

- **No IoU-based benchmark.** All scoring is human eyes on frames. There's no
  automated "run model X against a fixed ground-truth set and print mAP." That
  makes model-to-model comparison slow and slightly subjective.
- **No held-out test set.** Nothing structurally prevents a frame that trained
  v3 from being used to score v3. The deterministic train/valid split protects
  against re-run leakage, not against cross-generation leakage.
- **Correction boxes aren't harvested.** `eval_feedback.corrected_boxes` holds
  human-drawn boxes for exactly the otters the model missed — the most valuable
  labels in the system — and no exporter turns them into training data yet.
- **Clip diversity is the real bottleneck.** Sampling needs to spread across many
  clips, times of day, and tide conditions rather than taking many frames from
  few clips.
- **The review-queue pre-fill is only half-wired.** `pipeline.py` writes
  `labeled_by: "auto"` boxes, but the React labeling UI doesn't preload existing
  boxes yet, so reviewers still draw from scratch.
- **`preliminary-model/dataset/data.yaml` has stale paths**, pointing at an old
  `people-detector` directory. Fix it locally before training; it's checked in
  wrong.
- **The `.gitignore` dataset rules don't do anything.** `dataset/train/images/`
  and friends contain a slash, so git anchors them to the repo root and they
  never match `preliminary-model/dataset/`. The dataset is committed as a result.
  Prefix them with `**/` if excluding it was actually the intent.
- **The labeling UI hardcodes the Supabase project URL** in both `App.jsx` and
  `Admin.jsx` rather than reading it from the environment.
- **`upload_frames.py` and `yolo-test.py` hardcode local paths** from the
  machines they were written on. Both need an edit before use. Worse, the two
  upload scripts don't chain: `upload_frames.py` uploads to the bucket root while
  `upload_to_db.py` only lists the `otter_frames/` prefix, which is why the
  `db/populate.sql` fallback exists.
- **`requirements.txt` is incomplete.** `pipeline.py` imports `boto3`, the
  exporters import `requests` and `Pillow`, and the backend needs `uvicorn` —
  none of which are listed in the root or `public-labeling` requirements files.
  Installing from them alone leaves you short.
- **`pipeline.py`'s Supabase fallback only fires in `--eval` mode.** The
  `../public-labeling/.env` load lives inside the `if args.eval:` branch, so a
  normal auto-labeling run started from `auto-labeling/` finds no `.env` and
  disables the human-review route — uncertain-band frames get dropped rather than
  queued. It's at least visible: they're tallied under `review_no_supabase` in
  the run summary. Export the two vars yourself for a non-eval run.

---

## Conventions

- **Models are promoted, not referenced in place.** Scripts load
  `models/otter_vN.pt`. Nothing points into `runs/`.
- **Every external dependency is optional.** No S3 → the recorder keeps clips
  locally. No Supabase → the pipeline still builds a dataset. This is what makes
  the pieces testable alone.
- **The long-running services are configured by environment variable** with sane
  defaults, in a CONFIG block at the top of the file — `motion_recorder.py` and
  `pipeline.py`. The interactive tools use argparse instead. A handful of older
  scripts (`upload_frames.py`, `yolo-test.py`) still hardcode paths.
- **Pixel boxes are `{x, y, width, height}`** everywhere a human or a UI is
  involved; normalized `cx cy w h` only in YOLO label files. Conversion happens
  at the boundary.
- **Artifacts stay out of git.** `*.pt`, `*.mp4`, `runs/`, and `cookies.txt` are
  gitignored, so a fresh clone has no model weights. The seed Roboflow dataset is
  the exception — it *is* committed at `preliminary-model/dataset/`.
- **LF line endings are forced** by `.gitattributes`. A CRLF round-trip once
  showed ~300 files as modified; that's what the `* text=auto eol=lf` rule
  prevents.
