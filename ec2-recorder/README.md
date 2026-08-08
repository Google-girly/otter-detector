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
aws s3 mb s3://your-otter-clips --region us-east-2
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
- **Type:** `t3.small` (2 vCPU / 2 GB), no GPU needed. It's a *burstable*
  instance: ~40% total CPU sustained on credits, bursts spend a credit balance.
  720p idle-watching fits in baseline; encoding during long otter sessions
  bursts above it. Two things keep `t3.small` happy at 720p:
  - Run at **`FPS=10`** (see the service file) — lighter decode + encode.
  - Enable **T3 unlimited mode** so a credit crunch throttles your wallet a few
    cents instead of throttling the recorder:
    ```sh
    aws ec2 modify-instance-credit-specification \
      --instance-credit-specification "InstanceId=i-XXXX,CpuCredits=unlimited"
    ```
  If CPU is still pinned, drop to 480p (`WIDTH=854 HEIGHT=480`).
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
S3_BUCKET=your-otter-clips AWS_REGION=us-east-2 .venv/bin/python motion_recorder.py
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

## Run only during stream hours (7am–7pm Pacific)

The otter cam is only live 7am–7pm PT. There's no reason to pay for the instance
(or let it uselessly retry) overnight, so we auto-**start** it at 6:55am and
auto-**stop** it at 7:05pm. Because the recorder is a systemd service set to
`enable` (above), it launches by itself the moment the instance powers on — so
"turn the computer on" == "start recording." Nothing else to trigger.

This uses **EventBridge Scheduler**, which understands time zones directly, so it
follows Pacific daylight-saving changes on its own — no UTC math, no twice-a-year
fix. Run these once from your laptop (fill in your account ID and instance ID):

```sh
ACCOUNT_ID=123456789012          # aws sts get-caller-identity --query Account --output text
INSTANCE_ID=i-0abc123def456       # your recorder instance
REGION=us-east-2

# 1. A role the scheduler assumes, allowed only to start/stop this one instance.
aws iam create-role --role-name otter-scheduler-role \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow",
      "Principal":{"Service":"scheduler.amazonaws.com"},
      "Action":"sts:AssumeRole"}]}'

aws iam put-role-policy --role-name otter-scheduler-role \
  --policy-name start-stop-ec2 --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{\"Effect\":\"Allow\",
      \"Action\":[\"ec2:StartInstances\",\"ec2:StopInstances\"],
      \"Resource\":\"arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}\"}]}"

ROLE_ARN=arn:aws:iam::${ACCOUNT_ID}:role/otter-scheduler-role

# 2. Turn ON at 6:55am Pacific, every day.
aws scheduler create-schedule --name otter-cam-start \
  --schedule-expression "cron(55 6 * * ? *)" \
  --schedule-expression-timezone "America/Los_Angeles" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "{\"Arn\":\"arn:aws:scheduler:::aws-sdk:ec2:startInstances\",
             \"RoleArn\":\"${ROLE_ARN}\",
             \"Input\":\"{\\\"InstanceIds\\\":[\\\"${INSTANCE_ID}\\\"]}\"}"

# 3. Turn OFF at 7:05pm Pacific, every day.
aws scheduler create-schedule --name otter-cam-stop \
  --schedule-expression "cron(5 19 * * ? *)" \
  --schedule-expression-timezone "America/Los_Angeles" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "{\"Arn\":\"arn:aws:scheduler:::aws-sdk:ec2:stopInstances\",
             \"RoleArn\":\"${ROLE_ARN}\",
             \"Input\":\"{\\\"InstanceIds\\\":[\\\"${INSTANCE_ID}\\\"]}\"}"
```

Prefer clicking? Console → **EventBridge → Scheduler → Schedules → Create**, make
two schedules with the cron/timezone above, target **EC2 → StartInstances** /
**StopInstances**, and let it create the role for you.

**Why this also fixes the CPU worry:** the overnight shutdown gives the burstable
`t3.small` ~12 hours to fully recharge its CPU credits every night, so it starts
each morning at full strength. Cost drops too — you pay for ~12 hours/day, not 24.

Sanity checks: after 7pm the instance should show `stopped` in the EC2 console
(you're not billed for stopped compute); a couple minutes after 6:55am it should
be `running` and `journalctl -u otter-recorder -f` should show clips again.

## Local mode (no AWS at all)

`S3_BUCKET` is optional. Leave it unset and the recorder runs in **local mode**:
clips are written to `WORK_DIR` and kept on disk instead of being uploaded and
deleted. Add `PREVIEW=1` for a live window showing the frame, the motion mask,
and the current `motion_px` reading — the fastest way to tune sensitivity.

```sh
# from your laptop, using the repo venv — NOT the instance venv
PREVIEW=1 WORK_DIR=./test-clips ../.venv/bin/python motion_recorder.py   # 'q' quits
```

Run this on your laptop, not the instance. `requirements-ec2.txt` pins
`opencv-python-headless`, which has no `imshow` — `PREVIEW=1` against the
instance venv raises instead of opening a window. The repo's root
`requirements.txt` installs the full `opencv-python`, which does have it.

Note that `FPS` defaults to 15 in the script but the systemd unit overrides it to
10, which is what actually runs in production.

## Tuning (all via env vars — see the CONFIG block in the script)

| Var | Default | What it does |
|-----|---------|--------------|
| `YOUTUBE_URL` | the main otter cam | Which stream to record. The after-hours cam URL is noted in the script. |
| `S3_BUCKET` | *(unset)* | Upload target. **Unset = local mode**, clips stay in `WORK_DIR`. |
| `S3_PREFIX` | `clips` | Key prefix inside the bucket. |
| `AWS_REGION` | `us-east-2` | |
| `COOKIES_FILE` | *(unset)* | Path to a Netscape-format `cookies.txt` from a browser logged into YouTube. **Required on EC2** — datacenter IPs get the "confirm you're not a bot" gate. Unnecessary from home. |
| `WORK_DIR` | `/tmp/otter-clips` | Where clips are written before upload. |
| `PREVIEW` | `0` | `1` opens the live preview + motion-mask windows (local only). |
| `WARMUP_FRAMES` | `FPS × 5` | Frames to ignore while MOG2 learns the background. |
| `MOTION_AREA_FRAC` | `0.012` | Fraction of the frame that must change to count as motion. **Raise it** if water ripples trigger recording; lower it to catch smaller movement. |
| `VAR_THRESHOLD` | `40` | MOG2 sensitivity. Higher = less sensitive. |
| `PREROLL_SECONDS` | `3` | Seconds kept *before* motion started (so clips don't start mid-action). |
| `TAIL_SECONDS` | `15` | Keep recording this long after motion stops before closing the clip. Bigger = longer, fewer clips (won't split during brief pauses). |
| `MAX_CLIP_SECONDS` | `600` | Hard cap so one long event can't produce a giant file. |
| `FPS` | `15` | Record frame rate. Lower = smaller files, less CPU. |
| `WIDTH`/`HEIGHT` | `1280`/`720` | Capture resolution (720p). Drop to `854`/`480` to save CPU/bandwidth. |
| `HEARTBEAT_SECONDS` | `60` | How often to log a "still alive + current motion level" line while idle. |
| `CRF` | `20` | H.264 quality. **Lower = clearer + bigger files** (18 near-lossless, 23 smaller). |
| `ENCODER_PRESET` | `veryfast` | libx264 speed/compression tradeoff. `veryfast` keeps CPU low for a t3.small; `slow` = smaller files, more CPU. |
| `TZ_NAME` | `America/Los_Angeles` | Timezone used for clip names + S3 date folders. Handles daylight saving on its own. |

Clips are named `otters-YYYY-MM-DD-HHMM.mp4` in local (Pacific) time, and upload
to `clips/YYYY/MM/DD/` matching that name. Two clips in the same minute get a
`-2`, `-3` suffix so nothing is overwritten.

## Notes / gotchas

- **Water ripples:** the biggest false-positive source on a water cam. MOG2 learns
  the constant rippling as background within a few seconds (`WARMUP_FRAMES`), but if
  you still get spurious clips, raise `MOTION_AREA_FRAC` first.
- **Expiring stream URLs:** handled — if ffmpeg dies, the script re-resolves the
  YouTube URL and restarts, discarding any half-written clip.
- **Uploads are async:** each finished clip uploads in a background thread and is
  deleted locally on success, so recording never blocks on S3.
- **Codec:** clips are encoded with **H.264 (libx264)** by piping frames into a
  second ffmpeg process — clearer and smaller than opencv's built-in mp4v, and
  browser/QuickTime-friendly (`yuv420p`). This needs the system `ffmpeg` to
  include libx264, which Ubuntu's `apt install ffmpeg` provides. Tune quality with
  `CRF` and CPU cost with `ENCODER_PRESET`.
- **Cost ballpark:** `t3.small` on-demand ≈ $15/mo + S3 storage/egress. Use a
  spot instance or stop it overnight to cut that further.

## What happens to the clips

[`../auto-labeling`](../auto-labeling) picks them up from S3 — cutting them into
frames, running the model, and routing each frame to a training set or a human.
See [`../docs/PIPELINE.md`](../docs/PIPELINE.md) for the full loop.
