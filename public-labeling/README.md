# Otter Detector — Labeling Pipeline

A crowdsourced bounding box labeling tool for improving an otter detection ML model. Users are served unlabeled frames from a live YouTube stream, draw bounding boxes, and submit labels back to a central database.

---

## Project Structure

```
public-labeling/
├── main.py             # FastAPI backend
├── upload_to_db.py     # Script to register images in the database
├── db/
│   ├── schema.sql      # CREATE TABLE statements — run once in Supabase SQL Editor
│   └── populate.sql    # Backfill images table from Storage — run if upload_to_db.py returns 0
├── .env                # API keys (never commit this)
├── .env.example        # Template for .env
└── .gitignore
```

---

## Stack

| Part | Tech |
|---|---|
| Video download | yt-dlp |
| Frame extraction | FFmpeg |
| Storage | Supabase Storage |
| Database | Supabase (PostgreSQL) |
| Backend | Python + FastAPI |
| Frontend | React + react-konva (coming) |

---

## Step 1 — Download Video Footage

Install yt-dlp:

```bash
# Mac
brew install yt-dlp

# Windows
winget install yt-dlp
```

Download a specific time range from a YouTube stream:

```bash
# Grab first 5 minutes
yt-dlp --download-sections "*00:00:00-00:05:00" -o "~/Desktop/otter_clip.mp4" "YOUR_YOUTUBE_URL"

# Grab last 4 hours 11 minutes
yt-dlp --download-sections "*-04:11:15" -o "~/Desktop/otter_clip.mp4" "YOUR_YOUTUBE_URL"
```

---

## Step 2 — Extract Frames with FFmpeg

Install FFmpeg:

```bash
# Mac
brew install ffmpeg

# Windows
winget install ffmpeg
```

Create output folder and extract 1 frame per second:

```bash
mkdir ~/Desktop/otter_frames
ffmpeg -i ~/Desktop/otter_clip.mp4 -vf fps=1 -q:v 2 ~/Desktop/otter_frames/frame_%05d.jpg
```

- `-vf fps=1` — 1 frame per second
- `-q:v 2` — high quality JPEGs
- `frame_%05d.jpg` — names files `frame_00001.jpg`, `frame_00002.jpg`, etc.

---

## Step 3 — Supabase Setup

1. Create a free account at supabase.com
2. Create a new project (save your database password)
3. Go to **Storage** → create a bucket called `frames`, set to **Public**
4. Go to **Integrations → Data API → Settings** → expose your `images` and `labels` tables

---

## Step 4 — Database Tables

Run **`db/schema.sql`** in the Supabase SQL Editor. It creates two tables:

- `images` — tracks every frame and whether it has been labeled
- `labels` — stores bounding box data for each labeled image

---

## Step 5 — Upload Frames to Supabase Storage

Install the Supabase CLI:

```bash
# Mac
brew install supabase/tap/supabase

# Windows — download from https://github.com/supabase/cli/releases
```

Login and link your project:

```bash
supabase login
supabase link --project-ref YOUR_PROJECT_REF
```

Upload frames:

```bash
# Mac/Linux
supabase storage cp -r --experimental ~/Desktop/otter_frames/ ss:///frames/

# Windows (use full path)
supabase storage cp -r --experimental C:\Users\yourname\Desktop\otter_frames\ ss:///frames/
```

---

## Step 6 — Populate the Database from Storage

Run `upload_to_db.py` to register uploaded frames in the database. If it returns 0 rows, run **`db/populate.sql`** directly in the Supabase SQL Editor instead.

---

## Step 7 — Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Make sure `.env` is in your `.gitignore`:

```bash
echo ".env" >> .gitignore
```

Install dependencies:

```bash
pip3 install supabase python-dotenv fastapi uvicorn
```

---

## Step 8 — Run the Backend

The backend lives in **`main.py`** (FastAPI). Start it with:

```bash
uvicorn main:app --reload
```

Test it in your browser:

```
http://127.0.0.1:8000/next-image
```

---

## What's Next

- [ ] Build the React frontend with bounding box drawing (react-konva)
- [ ] Add user authentication so we know who labeled what
- [ ] Switch storage from Supabase to AWS S3 (when AWS credentials are available)
- [ ] Run existing otter model on frames to pre-filter empty frames

---

## Notes

- The Supabase **publishable key** (`sb_publishable_...`) is required — the old anon key will return empty results
- Tables must be exposed in **Integrations → Data API → Settings** or the API won't see them
- When switching to AWS S3, only the storage upload step and image URLs need to change — the database stays the same
