# Southern Sea Otter Detector

A YOLO object-detection model fine-tuned to find Southern Sea Otters on the
Monterey Bay Aquarium's live otter cam — plus the infrastructure around it that
records footage, gets it labeled, scores the model, and feeds the next version.

```
YouTube otter cam
      │
      ▼
ec2-recorder/        motion-triggered clips ──▶ S3
      │
      ▼
auto-labeling/       clips ▸ frames ▸ model boxes ▸ routed by confidence
      │                                    │
      │ high conf                          │ uncertain / eval
      ▼                                    ▼
  YOLO dataset                    public-labeling/   humans draw + judge boxes
      │                                    │
      └──────────────▶ train ◀─────────────┘
                         │
                         ▼
                   models/otter_vN.pt
```

**Read [`docs/PIPELINE.md`](docs/PIPELINE.md) for how the whole thing works
end to end** — what each stage does, why it's built that way, and how to run a
full cycle. This page is just the map.

## Directories

| Directory | What lives there | Docs |
|---|---|---|
| `preliminary-model/` | The original fine-tune, plus laptop scripts for watching a model run on a live stream or recorded clips | [README](preliminary-model/README.md) |
| `ec2-recorder/` | The always-on recorder: watches the cam, detects motion, uploads one clip per event to S3 | [README](ec2-recorder/README.md) |
| `auto-labeling/` | The loop-closer: pulls clips from S3, cuts frames, runs the model, and routes each frame to auto-accept / human review / eval | [README](auto-labeling/README.md) |
| `public-labeling/` | Everything human-facing: the crowd labeling web app, the model review pages, the exporters, and the FastAPI backend | [README](public-labeling/README.md) |
| `models/` | Promoted model weights — `otter_v1.pt`, `otter_v2.pt`. Gitignored, so **a fresh clone won't have them**; train them or copy them in. | — |
| `runs/` | Ultralytics training output — charts, sample batches, weights. Local only, gitignored. | — |

## Models

| Model | Character |
|---|---|
| `otter_v1.pt` | Aggressive. High recall, more false alarms. |
| `otter_v2.pt` | Conservative. Higher precision, misses about half the otters — mostly ones in the water. |

They're different operating points, not a strict upgrade. On the same 272-frame
clip, v1 drew 388 boxes and v2 drew 174. Current measured numbers and their
caveats are in [`docs/PIPELINE.md`](docs/PIPELINE.md#where-the-models-stand).

## Quickstart

```bash
git clone <your-repo-url>
cd otter-detector

python3 -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# watch otter_v2 on the live cam
cd preliminary-model && python yt-test2.py     # 'q' to quit
```

What's in the clone and what isn't: the seed Roboflow dataset **is** committed at
`preliminary-model/dataset/`. Model weights (`*.pt`), `runs/`, `*.mp4`, and
`cookies.txt` are gitignored — you'll need to train or copy in a model before the
detection scripts will run.

## What you need for which part

| To do this | You need |
|---|---|
| Run a model on the live stream or a local clip | Python venv + `ffmpeg` |
| Record clips | An EC2 instance, an S3 bucket, an IAM instance role, YouTube `cookies.txt` |
| Pull clips / auto-label / evaluate | AWS credentials (`aws sts get-caller-identity` works) |
| Anything human-facing | A Supabase project + `public-labeling/.env` |

## Dataset

The seed training set is on Roboflow Universe:
[sea-otters-dslyz-kperf](https://universe.roboflow.com/desiree-torres/sea-otters-dslyz-kperf)
— 347 annotated images, one class (`Southern Sea Otter`), YOLOv8 format,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Everything since has
come from the recorder → labeling loop.
