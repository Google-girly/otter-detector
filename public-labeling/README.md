# public-labeling — humans in the loop

Everything a person touches. Two distinct jobs live here, and they're kept
deliberately separate:

1. **Labeling** — crowdsourced bounding boxes that *become training data*.
   React app + FastAPI + Supabase `images`/`labels` tables.
2. **Evaluation** — humans *judging a model's output* to get precision/recall.
   Two review pages + Supabase `eval_predictions`/`eval_feedback` tables.

Mixing them would pollute the training set with the model's own guesses, so they
never share a table.

---

## Contents

```
public-labeling/
├── main.py               # FastAPI backend — labeling routes, admin routes, eval routes
├── api/index.py          # Vercel entrypoint (just `from main import app`)
├── labeling-ui/          # React + Vite + react-konva crowd labeling app
├── review.html           # Eval page: score one model's frames (talks to main.py)
├── clip_review.html      # Eval page: two models side by side on one clip (offline)
├── clip_predict.py       # Per-frame predictions for clip_review.html -> manifest.json
├── export_annotated.py   # Draw v2 boxes + human corrections onto eval frames
├── annotate_model.py     # Draw ANY model's boxes onto the same eval frames
├── export_dataset.py     # Labeled images + boxes -> YOLO dataset
├── upload_frames.py      # Bulk-upload frames to Supabase Storage (resumable)
├── upload_to_db.py       # Register uploaded frames in the images table
├── db/
│   ├── schema.sql        # images + labels
│   ├── add_has_otter.sql # migration for pre-existing labels tables
│   ├── populate.sql      # backfill images from Storage if upload_to_db.py returns 0
│   └── eval_schema.sql   # eval_predictions + eval_feedback (+ grants + migrations)
├── vercel.json           # build config: Vite frontend + Python API
├── .env / .env.example
└── requirements.txt
```

## Stack

| Part | Tech |
|---|---|
| Video download | yt-dlp |
| Frame extraction | FFmpeg |
| Storage | Supabase Storage (public `frames` bucket) |
| Database | Supabase (PostgreSQL) |
| Backend | Python + FastAPI |
| Labeling frontend | React 19 + Vite + react-konva |
| Eval frontends | Two single-file HTML pages, no build step |
| Hosting | Vercel |

---

# Part 1 — Labeling

## Volunteer accounts (new deployment)

Labelers sign up with email + password (Supabase Auth). Every label is credited
to the volunteer who made it, so they can see their own count, hours and rank,
and the admin page has a **Volunteers** tab with a CSV export for signing off
community-service hours.

- **Hours** = time each image was on screen until submit, capped at 2 min per
  image (so an idle tab doesn't count).
- The **leaderboard** shows first name + last initial only; full names and
  emails are visible to the admin only.
- Images are claimed atomically on submit: if two people label the same frame,
  only the first gets the credit (the second just gets the next image).
- Admin corrections edit the boxes in place, so the volunteer keeps the credit.

How it fits together: the browser uses the **publishable** key, for Auth only.
It sends its access token to FastAPI, which verifies it with
`supabase.auth.get_user()` and does all the table reads and writes with the
**service** key. Every table has RLS on with no policies, so nobody can reach
the data straight from the browser.

**Setup on the new project (`rxdnubvmfomorzppjlxz`):**

1. SQL Editor → run `db/volunteer_schema.sql` (images, profiles, labels,
   the `volunteer_totals` view, and a signup trigger that creates profiles).
2. Storage → create a public `frames` bucket and upload frames (see below).
3. Authentication → URL Configuration → set **Site URL** to the deployed
   domain, and add it under Redirect URLs (the confirmation and password-reset
   links go there).
4. Authentication → Email: the built-in mailer only sends a few emails per hour.
   For a public signup, either set up custom SMTP (e.g. Resend, which is free
   at low volume) or turn off **Confirm email**.
5. `labeling-ui/.env`: copy `.env.example` and fill in `VITE_SUPABASE_URL` and
   `VITE_SUPABASE_PUBLISHABLE_KEY`.
6. New Vercel project, root directory `public-labeling`. Env vars:
   `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ADMIN_PASSWORD`,
   `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`.

| Route | Purpose |
|---|---|
| `GET /api/me` | Your totals: labels, today, hours, rank |
| `GET /api/leaderboard` | Top 10 by labels |
| `GET /api/admin/volunteers` | All volunteers with labels + hours (admin) |

`/api/next-image` and `/api/submit-label` now require a signed-in user.

## Setup

### 1. Get frames

Either let [`../ec2-recorder`](../ec2-recorder) produce clips, or pull footage by
hand:

```bash
brew install yt-dlp ffmpeg          # Windows: winget install yt-dlp / ffmpeg

# first 5 minutes of a stream
yt-dlp --download-sections "*00:00:00-00:05:00" -o "~/Desktop/otter_clip.mp4" "YOUR_YOUTUBE_URL"

# 1 frame per second, high-quality JPEGs
mkdir ~/Desktop/otter_frames
ffmpeg -i ~/Desktop/otter_clip.mp4 -vf fps=1 -q:v 2 ~/Desktop/otter_frames/frame_%05d.jpg
```

### 2. Supabase project

1. Create a project at supabase.com (save the database password).
2. **Storage** → create a bucket named `frames`, set it **Public**.
3. Run `db/schema.sql` in the SQL Editor (creates `images` + `labels`).
4. **Integrations → Data API → Settings** → expose `images` and `labels`.

If the `labels` table already existed without `has_otter`, run
`db/add_has_otter.sql` once.

### 3. Environment

```bash
cp .env.example .env      # then fill it in
pip install -r requirements.txt
```

| Var | Used for |
|---|---|
| `SUPABASE_URL` | project URL |
| `SUPABASE_KEY` | publishable key (`sb_publishable_…`) — used by `main.py`, `upload_to_db.py`, and `export_dataset.py` |
| `SUPABASE_SERVICE_KEY` | service-role key — `upload_frames.py` (Storage uploads) and the eval exporters, which fall back to `SUPABASE_KEY` if it's unset. Never ship this to a browser. |
| `ADMIN_PASSWORD` | gate for the `/api/admin/*` routes |

`requirements.txt` here is incomplete — it lists `fastapi`, `supabase`,
`python-dotenv`, `requests`, `Pillow`, but the backend also needs `uvicorn`, and
`clip_predict.py` needs `ultralytics` and `opencv-python`. Install from the root
`requirements.txt` too, and add `uvicorn` if it's missing.

### 4. Upload frames

Either the Supabase CLI:

```bash
brew install supabase/tap/supabase        # Windows: github.com/supabase/cli/releases
supabase login
supabase link --project-ref YOUR_PROJECT_REF
supabase storage cp -r --experimental ~/Desktop/otter_frames/ ss:///frames/
```

…or `upload_frames.py`, which is resumable (it lists what's already in the
bucket and skips it) and retries 3× per file. Edit `FRAMES_DIR` at the top first.

### 5. Register them in the database

```bash
python upload_to_db.py
```

**This usually inserts 0 rows.** `upload_to_db.py` only lists the `otter_frames/`
prefix and writes `storage_path` as `frames/otter_frames/<name>`, while
`upload_frames.py` uploads to the bucket *root*. Unless your frames actually
landed under `otter_frames/`, run `db/populate.sql` in the SQL Editor instead —
it backfills `images` straight from `storage.objects` regardless of prefix.

## Running it

```bash
uvicorn main:app --reload                          # backend on :8000
cd labeling-ui && npm install && npm run dev       # frontend on :5173
```

Check `http://127.0.0.1:8000/api/next-image` to confirm the backend sees data.

## API

All routes are under `/api` so they don't collide with the frontend on Vercel.

| Route | Purpose |
|---|---|
| `GET /api/next-image` | A random unlabeled image (picked from a batch of 50, so concurrent labelers rarely collide) |
| `POST /api/submit-label` | Save boxes, mark the image labeled |
| `POST /api/admin/login` | Exchange `ADMIN_PASSWORD` for a bearer token |
| `GET /api/admin/stats` | Total / labeled / unlabeled / percent complete |
| `GET /api/admin/images` | Every image with its boxes, newest first |
| `PUT /api/admin/images/{image_id}` | Replace an image's boxes (admin correction) |
| `GET /api/eval/next` | Next prediction with no feedback row (see Part 2) |
| `POST /api/eval/feedback` | Record one human verdict, as per-frame counts |
| `GET /api/eval/stats` | Live box-level precision / recall scoreboard |

Admin routes require `Authorization: Bearer <ADMIN_PASSWORD>`, compared with
`secrets.compare_digest`. The `/api/eval/*` routes are unauthenticated.

## Export to a training set

```bash
python export_dataset.py
```

Pulls every `is_labeled` image plus its boxes, downloads the originals, does an
80/20 train/valid split, converts pixel boxes to normalized YOLO lines (handling
boxes drawn right-to-left or bottom-to-top), and writes `dataset/` with a
`data.yaml`. Images with no boxes get an empty label file — a valid YOLO
background sample. A fine-tune command is printed at the end, but it hardcodes
`otter_v1.pt` as the starting model — change it to whatever you're actually
fine-tuning from.

## Deploying

`vercel.json` builds `labeling-ui` with Vite and serves `main.py` through
`api/index.py`; `/api/*` rewrites to the Python function and everything else
falls through to `index.html`. Set the four env vars in the Vercel project
settings.

---

# Part 2 — Model evaluation

Two pages, for two different questions.

## `review.html` — score one model, one frame at a time

Used with `../auto-labeling/pipeline.py --eval`, which uploads sampled frames and
the model's predictions to `eval_predictions`.

**One-time:** run `db/eval_schema.sql` in the SQL Editor and expose both tables
under **Integrations → Data API → Settings**. The file ends with the later
migrations (`corrected_boxes`, the per-frame count columns, the widened verdict
constraint) written as `ADD COLUMN IF NOT EXISTS` — but the `CREATE TABLE` and
`CREATE INDEX` statements at the top are *not* guarded, so re-running the whole
file on an existing database errors with *relation already exists*. Run it once;
after that, apply only the trailing migration block.

```bash
uvicorn main:app --reload      # then open review.html in a browser
```

For each frame you see the model's boxes and record **per-frame counts**: how
many boxes were correct, and how many otters it missed (you draw those). The
backend derives everything else:

| Counts | Derived verdict |
|---|---|
| no boxes, nothing missed | `correctly_empty` (True Negative) |
| all boxes correct, nothing missed | `correct` (True Positive) |
| boxes, none correct, nothing missed | `false_alarm` (False Positive) |
| no boxes, otters missed | `missed` (False Negative) |
| more than one of the above | `mixed` |

There's also a `box_off` flag: right otter, sloppy box. `GET /api/eval/stats`
returns a live **box-level** precision/recall — counting boxes rather than
frames, so multi-otter frames score correctly.

## `clip_review.html` — two models side by side on one clip

Answers "which model is better on *this* footage" by scrubbing a clip frame by
frame with both models' boxes visible. Entirely offline: it reads a
`manifest.json` and local JPEG folders, saves your scores to `localStorage`, and
exports JSON + CSV. No backend, no database.

### Building a clip eval directory

```bash
CLIP=~/Desktop/otter-eval-0724-0907
mkdir -p $CLIP/raw $CLIP/v1 $CLIP/v2

# 1. get the source clip
aws s3 cp s3://otter-clips/clips/2026/07/24/otters-2026-07-24-0907.mp4 $CLIP/raw.mp4

# 2. annotated copies, one per model
cd ../preliminary-model
../.venv/bin/python clip-test.py --model ../models/otter_v1.pt --out $CLIP $CLIP/raw.mp4
../.venv/bin/python clip-test.py --model ../models/otter_v2.pt --out $CLIP $CLIP/raw.mp4

# 3. explode all three videos into 4-digit frames
ffmpeg -i $CLIP/raw.mp4               -q:v 2 $CLIP/raw/%04d.jpg
ffmpeg -i $CLIP/raw-otter_v1.mp4      -q:v 2 $CLIP/v1/%04d.jpg
ffmpeg -i $CLIP/raw-otter_v2.mp4      -q:v 2 $CLIP/v2/%04d.jpg

# 4. per-frame box data
cd ../public-labeling
../.venv/bin/python clip_predict.py $CLIP --clip $CLIP/raw.mp4 \
    --model ../models/otter_v1.pt --model ../models/otter_v2.pt --conf 0.4

# 5. serve it
cp clip_review.html $CLIP/index.html
cd $CLIP && python3 -m http.server
```

ffmpeg's `%04d` starts at 1 while the page indexes from 0, so shift the frame
folders down by one (or extract with `-start_number 0`) if the boxes look one
frame late.

The expected layout:

```
otter-eval-0724-0907/
├── index.html        # copy of clip_review.html
├── manifest.json     # from clip_predict.py
├── raw.mp4
├── raw/  0000.jpg …  # clean frames, no boxes  (press 'x' to toggle)
├── v1/   0000.jpg …  # otter_v1's boxes burned in
└── v2/   0000.jpg …  # otter_v2's boxes burned in
```

Folder names come from the model name with `otter_` stripped, so
`otter_v3.pt` → `v3/`.

**Why `clip_predict.py` decodes with cv2 instead of reading the extracted
JPEGs:** it has to match `clip-test.py`'s code path exactly. Predicting on
re-encoded JPEGs shifts borderline detections (a 0.44-conf box fell under a 0.4
threshold), and then the counts on screen would disagree with the boxes the
reviewer can see.

**Keys:** `1` accept both · `0` both wrong · `q`/`w` left-model correct −/+ ·
`e`/`r` left missed +/− · `u`/`i` and `o`/`p` for the right model · `x` toggle
clean frames · `←`/`→` step by the current stride.

Stride defaults to every 5th frame — at 15fps, adjacent frames are nearly
identical, so scoring all of them buys precision you don't need.

## Exporters

Both write local folders you can scroll through or pull figures from.

```bash
python export_annotated.py                                    # -> annotated/
python annotate_model.py --model ../models/otter_v1.pt --out annotated_v1
```

`export_annotated.py` draws **v2's boxes in the verdict's color** (green
correct, orange missed, red false alarm, blue box-off, gray correctly-empty,
purple mixed) plus your cyan correction boxes, with a header strip carrying the
verdict and TP/FP/FN counts. Output is grouped `annotated/<verdict>/`.

`annotate_model.py` pulls the *same* clean frames from Storage, runs whatever
model you point it at, and groups the output by **v2's** verdict — so
`annotated_v1/missed/` and `annotated/missed/` hold the same frames and you can
compare folder for folder. Match the confidence thresholds (`--conf 0.25`, the
eval default) or the comparison isn't fair.

Both take `--no-coords` to drop the `x,y w×h` text from box labels.

---

## Notes / gotchas

- The Supabase **publishable key** (`sb_publishable_…`) is required — the old
  anon key returns empty results.
- Tables must be exposed under **Integrations → Data API → Settings** or
  PostgREST won't see them.
- `eval_schema.sql`'s `GRANT` block matters: tables created in the SQL Editor are
  owned by `postgres`, and without the grants PostgREST returns
  *permission denied for table* (42501).
- `clip_review.html` must be served over HTTP. Opening the file directly makes
  the `manifest.json` fetch fail, and the page says so.
- `SUPABASE_SERVICE_KEY` bypasses row-level security. Backend and scripts only —
  never the browser bundle.
- Storage in a future version may move to S3; only the upload step and the image
  URLs would change, since the database only stores paths.
