# EC2 Motion-Triggered Otter Recorder

Records the YouTube otter cam and uploads **one video clip per motion event** to
S3. Motion is detected with an OpenCV background subtractor (CPU-only, no GPU / no
YOLO), so this runs happily on a small instance.

```
YouTube live ──▶ ffmpeg (raw frames) ──▶ motion_recorder.py
                                             │  MOG2 background subtraction
                                             │  pre-roll buffer + tail
                                             ▼
                                        clip.mp4 ──▶ S3 (date-partitioned)
```

## AWS setup (one time)

### 1. S3 bucket
```sh
aws s3 mb s3://your-otter-clips --region us-west-2
```

### 2. IAM role for the instance
Create an IAM **role** (not access keys — the instance gets creds from its role,
which is why the script never mentions credentials) with this policy, and attach
it to the EC2 instance as its *instance profile*:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject"],
    "Resource": "arn:aws:s3:::your-otter-clips/*"
  }]
}
```

### 3. Launch an instance
- **AMI:** Ubuntu 22.04 or 24.04 LTS
- **Type:** `t3.small` is plenty for 480p motion detection. Bump to `t3.medium`
  if you raise resolution/FPS. No GPU needed.
- **Storage:** 20 GB gp3 is fine — clips are deleted locally right after upload,
  so disk only holds in-flight clips.
- Attach the instance profile from step 2. No security-group inbound rules are
  required (the recorder only makes outbound connections).

## Instance provisioning

SSH in, then:

```sh
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg libgl1

sudo mkdir -p /opt/otter-recorder
sudo chown ubuntu:ubuntu /opt/otter-recorder
cd /opt/otter-recorder

# copy motion_recorder.py + requirements-ec2.txt here (scp, git clone, or S3)
python3 -m venv .venv
.venv/bin/pip install -r requirements-ec2.txt
```

Quick manual test before installing the service:
```sh
S3_BUCKET=your-otter-clips AWS_REGION=us-west-2 .venv/bin/python motion_recorder.py
# wave something in front of the cam / wait for otter activity, then check:
aws s3 ls s3://your-otter-clips/clips/ --recursive
```

## Run it as a service (auto-start + auto-restart)

```sh
sudo cp otter-recorder.service /etc/systemd/system/
sudo nano /etc/systemd/system/otter-recorder.service   # set S3_BUCKET / region
sudo systemctl daemon-reload
sudo systemctl enable --now otter-recorder
journalctl -u otter-recorder -f                          # watch the logs
```

## Tuning (all via env vars — see the CONFIG block in the script)

| Var | Default | What it does |
|-----|---------|--------------|
| `MOTION_AREA_FRAC` | `0.004` | Fraction of the frame that must change to count as motion. **Raise it** if water ripples trigger recording; lower it to catch smaller movement. |
| `VAR_THRESHOLD` | `40` | MOG2 sensitivity. Higher = less sensitive. |
| `PREROLL_SECONDS` | `3` | Seconds kept *before* motion started (so clips don't start mid-action). |
| `TAIL_SECONDS` | `4` | Keep recording this long after motion stops (avoids chopping a clip into pieces during brief pauses). |
| `MAX_CLIP_SECONDS` | `600` | Hard cap so one long event can't produce a giant file. |
| `FPS` | `15` | Record frame rate. Lower = smaller files, less CPU. |
| `WIDTH`/`HEIGHT` | `854`/`480` | Capture resolution. |

## Notes / gotchas

- **Water ripples:** the biggest false-positive source on a water cam. MOG2 learns
  the constant rippling as background within a few seconds (`WARMUP_FRAMES`), but if
  you still get spurious clips, raise `MOTION_AREA_FRAC` first.
- **Expiring stream URLs:** handled — if ffmpeg dies, the script re-resolves the
  YouTube URL and restarts, discarding any half-written clip.
- **Uploads are async:** each finished clip uploads in a background thread and is
  deleted locally on success, so recording never blocks on S3.
- **Codec:** `mp4v` in an `.mp4` container (bundled with opencv). If you need H.264
  (smaller / better browser support), transcode in a Lambda triggered by the S3
  upload, or install a full ffmpeg build and pipe through it.
- **Cost ballpark:** `t3.small` on-demand ≈ $15/mo + S3 storage/egress. Use a
  spot instance or stop it overnight to cut that further.
```
