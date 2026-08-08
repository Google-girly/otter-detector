# Preliminary model — training & manual testing

The original YOLO fine-tune and the hand-driven scripts used to watch it work.
Everything here is *interactive*: you run it on your laptop, a window opens, you
look at it. The unattended parts of the system live in `../ec2-recorder`,
`../auto-labeling`, and `../public-labeling`.

## Scripts

| Script | What it does |
|---|---|
| `train.py` | Fine-tunes the base YOLO model on `dataset/data.yaml` — 100 epochs, 640px |
| `train-mac.py` | Same, plus `device="mps"` to use the Apple Silicon GPU |
| `yolo-test.py` | Runs the **base** (un-fine-tuned) model on one image and shows the result |
| `yt-test.py` | Live YouTube stream through **otter_v1**, window titled *Otter Cam 1* |
| `yt-test2.py` | Live YouTube stream through **otter_v2**, window titled *Otter Cam 2* |
| `stream-test.py` | Stream viewer with **no model** — proves the ffmpeg-pipe reconnect logic works |
| `clip-test.py` | Runs a model over *recorded* clips (local or S3) and writes annotated mp4s |

`yt-test.py` and `yt-test2.py` are deliberately near-identical so you can run
both at once and watch v1 and v2 disagree side by side.

## Setup

```bash
python3 -m venv .venv                     # from the repo root
source .venv/bin/activate                 # Windows: .\.venv\Scripts\Activate.ps1
pip install -r ../requirements.txt
```

> **Windows:** if activating the venv errors on execution policy, run
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once.

### Dataset

The seed dataset **is committed** at `dataset/` — 308 train and 39 valid images
with labels. (The root `.gitignore` has `dataset/train/images/` entries that look
like they'd exclude it, but gitignore patterns containing a slash anchor to the
repo root, so they never matched `preliminary-model/dataset/`.)

To re-export it fresh, pull it from Roboflow in **YOLOv8 format**:

- [sea-otters-dslyz-kperf on Roboflow Universe](https://universe.roboflow.com/desiree-torres/sea-otters-dslyz-kperf)
- 347 annotated images · 1 class (`Southern Sea Otter`) · CC BY 4.0

Either way, **fix the paths in `dataset/data.yaml`** — the committed copy points
at an old `people-detector` directory that no longer exists. Set `train`, `val`,
and `test` to absolute paths under your own checkout, e.g.
`/Users/you/otter-detector/preliminary-model/dataset/train/images`. Use forward
slashes on every OS.

### Base weights

`train.py`, `train-mac.py`, and `yolo-test.py` load `yolo26n.pt` from the
working directory. Download it, or edit those files to use one of ultralytics'
bundled checkpoints (e.g. `yolov8n.pt`), which downloads automatically.

## Training

```bash
python train.py          # or train-mac.py on Apple Silicon
```

Weights land in `runs/detect/train/weights/best.pt`. Promote a keeper by copying
it to `../models/otter_v3.pt` — the rest of the repo loads models from there by
name, not from `runs/`.

## Watching the model run

**Live stream:**
```bash
python yt-test2.py       # v2; yt-test.py for v1
```
Press `q` to quit. Both scripts hardcode `device='mps'` — change it to `'cpu'`
(or `0` for CUDA) if you're not on a Mac. The YouTube URL is hardcoded near the
top; edit it to point at a different cam.

On top of ultralytics' usual box + confidence label, both scripts draw a
**coordinate tag** below each box (`x:412 y:233`) in the box's own color, so you
can read off pixel positions while watching.

**Recorded clips** (headless — writes files instead of opening a window):
```bash
# newest 3 clips from S3, annotated onto the Desktop
../.venv/bin/python clip-test.py --s3 3 --out ~/Desktop/otter-annotated

# one specific S3 key
../.venv/bin/python clip-test.py --s3-key clips/2026/07/24/otters-2026-07-24-0907.mp4

# local files, with the v1 model
../.venv/bin/python clip-test.py --model ../models/otter_v1.pt some-clip.mp4
```

| Flag | Default | Meaning |
|---|---|---|
| `--model` | `../models/otter_v2.pt` | which weights to run |
| `--conf` | `0.4` | confidence threshold |
| `--device` | `mps` | `mps`, `cpu`, or `0` for CUDA |
| `--out` | `~/Desktop/otter-annotated` | where annotated mp4s land |
| `--cache` | `clip-cache` | where S3 downloads are kept |

Output is H.264 (`libx264`, `yuv420p`) so QuickTime and browsers can play it.
Each frame gets a HUD line (`frame 128 | 2 box(es)`) and per-clip stats are
printed at the end: frames, % of frames with an otter, total boxes.

> **Windows:** OpenCV's `imshow` won't render under WSL or headless sessions —
> run the live scripts natively in PowerShell. `clip-test.py` is headless by
> design and works anywhere.

## Where this fits

`clip-test.py` is the bridge to the rest of the repo: the clips it reads are the
ones `../ec2-recorder` uploads, and the annotated mp4s it produces are the input
to the side-by-side model comparison in `../public-labeling/clip_review.html`.
See [`../docs/PIPELINE.md`](../docs/PIPELINE.md) for the whole loop.
