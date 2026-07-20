"""
Motion-triggered live-stream recorder for the otter cam.

Reads a YouTube live stream via ffmpeg (raw frames over a pipe — same pattern as
stream-test.py, which survives YouTube's expiring HLS URLs), watches for motion
with an OpenCV background subtractor, and writes one video clip per motion event
to S3.

State machine:
    IDLE       -> keep a rolling pre-roll buffer, watch for motion
    RECORDING  -> write frames to a clip; keep going until the frame has been
                  quiet for TAIL_SECONDS (or MAX_CLIP_SECONDS is hit)
    (on stop)  -> close the file, upload to S3 in a background thread, delete local

Config is read from environment variables (see the CONFIG block). Nothing is
hardcoded that you'd want to change per-deployment.
"""

import collections
import datetime as dt
import os
import subprocess
import threading
import time

import cv2
import numpy as np
import yt_dlp

# ── Config (all overridable via environment variables) ──────────────────────────
YOUTUBE_URL   = os.environ.get("YOUTUBE_URL", "https://www.youtube.com/watch?v=abbR-Ttd-cA")
# S3_BUCKET is OPTIONAL. Leave it unset to run in LOCAL mode: clips are written to
# WORK_DIR and kept on disk instead of being uploaded/deleted. Great for testing.
S3_BUCKET     = os.environ.get("S3_BUCKET")
S3_PREFIX     = os.environ.get("S3_PREFIX", "clips")          # key prefix inside the bucket
AWS_REGION    = os.environ.get("AWS_REGION", "us-west-2")

# Show a live preview window with the motion mask (local testing only — needs a GUI,
# so keep it off on a headless server). Set PREVIEW=1 to enable.
PREVIEW       = os.environ.get("PREVIEW", "0") == "1"

WIDTH         = int(os.environ.get("WIDTH", 854))
HEIGHT        = int(os.environ.get("HEIGHT", 480))
FPS           = int(os.environ.get("FPS", 15))               # output/record frame rate
FRAME_SIZE    = WIDTH * HEIGHT * 3                            # bytes per raw bgr24 frame

# Motion sensitivity
MOTION_AREA_FRAC = float(os.environ.get("MOTION_AREA_FRAC", 0.004))  # fraction of frame that must change
VAR_THRESHOLD    = int(os.environ.get("VAR_THRESHOLD", 40))          # MOG2 sensitivity (higher = less sensitive)
WARMUP_FRAMES    = int(os.environ.get("WARMUP_FRAMES", FPS * 5))     # let the background model settle first

# Clip shaping
PREROLL_SECONDS   = float(os.environ.get("PREROLL_SECONDS", 3))      # seconds kept before motion started
TAIL_SECONDS      = float(os.environ.get("TAIL_SECONDS", 4))         # keep recording this long after motion stops
MAX_CLIP_SECONDS  = float(os.environ.get("MAX_CLIP_SECONDS", 600))   # hard cap so a clip can't run forever

WORK_DIR = os.environ.get("WORK_DIR", "/tmp/otter-clips")
os.makedirs(WORK_DIR, exist_ok=True)

FOURCC = cv2.VideoWriter_fourcc(*"mp4v")

# Only create an S3 client (and require boto3) when we actually have a bucket.
s3 = None
if S3_BUCKET:
    import boto3
    s3 = boto3.client("s3", region_name=AWS_REGION)


# ── Stream plumbing ─────────────────────────────────────────────────────────────
def get_stream_url():
    """Resolve a fresh direct stream URL. YouTube's URLs expire, so we re-resolve
    whenever ffmpeg dies."""
    ydl_opts = {"quiet": True, "format": f"best[height<={HEIGHT}]/best"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(YOUTUBE_URL, download=False)
        return info["url"]


def start_ffmpeg(url):
    """Start ffmpeg piping normalized raw BGR frames to stdout."""
    cmd = [
        "ffmpeg",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", url,
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-an",
        "-r", str(FPS),
        "pipe:1",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)


def read_frame(process):
    """Read exactly one frame worth of bytes off the pipe. Returns an ndarray or
    None if the stream ended / ffmpeg died."""
    raw = process.stdout.read(FRAME_SIZE)
    if len(raw) < FRAME_SIZE:
        return None
    return np.frombuffer(raw, np.uint8).reshape((HEIGHT, WIDTH, 3))


# ── S3 upload ────────────────────────────────────────────────────────────────────
def upload_and_cleanup(local_path):
    """Upload one finished clip to S3 (date-partitioned key) then delete it locally.
    Runs in a background thread so recording isn't blocked by the upload.

    In LOCAL mode (no S3_BUCKET) this is a no-op — the clip stays on disk in WORK_DIR."""
    if s3 is None:
        print(f"[local] kept clip {local_path}", flush=True)
        return
    fname = os.path.basename(local_path)
    now = dt.datetime.utcnow()
    key = f"{S3_PREFIX}/{now:%Y/%m/%d}/{fname}"
    try:
        s3.upload_file(local_path, S3_BUCKET, key, ExtraArgs={"ContentType": "video/mp4"})
        print(f"[s3] uploaded s3://{S3_BUCKET}/{key}", flush=True)
    except Exception as e:  # noqa: BLE001 — never let an upload error kill the recorder
        print(f"[s3] upload FAILED for {fname}: {e}", flush=True)
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────────
def main():
    dest = f"s3://{S3_BUCKET}/{S3_PREFIX}/" if s3 else f"{WORK_DIR}/ (LOCAL mode — no upload)"
    print(f"[boot] recording {YOUTUBE_URL} -> {dest}", flush=True)
    if PREVIEW:
        print("[boot] preview window on — press 'q' in the window to quit", flush=True)

    # detectShadows=False keeps water-ripple shadows from tripping motion; MOG2
    # adapts to the constantly-rippling water and treats it as background over time.
    backsub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=VAR_THRESHOLD, detectShadows=False)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    min_motion_pixels = MOTION_AREA_FRAC * WIDTH * HEIGHT

    preroll = collections.deque(maxlen=int(PREROLL_SECONDS * FPS))

    process = None
    writer = None
    clip_path = None
    clip_start = 0.0
    last_motion = 0.0
    frames_seen = 0

    def close_clip():
        nonlocal writer, clip_path
        if writer is not None:
            writer.release()
            print(f"[clip] closed {os.path.basename(clip_path)}", flush=True)
            threading.Thread(target=upload_and_cleanup, args=(clip_path,), daemon=True).start()
            writer = None
            clip_path = None

    while True:
        # (Re)start ffmpeg with a fresh URL whenever it isn't running.
        if process is None or process.poll() is not None:
            close_clip()  # don't leave a half-written clip across a reconnect
            try:
                url = get_stream_url()
                process = start_ffmpeg(url)
                print("[stream] ffmpeg (re)started", flush=True)
                backsub = cv2.createBackgroundSubtractorMOG2(
                    history=500, varThreshold=VAR_THRESHOLD, detectShadows=False
                )
                frames_seen = 0
            except Exception as e:  # noqa: BLE001
                print(f"[stream] could not start: {e}; retrying in 10s", flush=True)
                time.sleep(10)
                continue

        frame = read_frame(process)
        if frame is None:
            print("[stream] frame read failed — restarting ffmpeg", flush=True)
            process.kill()
            process = None
            continue

        frames_seen += 1
        now = time.time()

        # --- motion detection (on a downscaled gray frame for speed) ---
        small = cv2.resize(frame, (WIDTH // 2, HEIGHT // 2))
        mask = backsub.apply(small)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        motion_pixels = cv2.countNonZero(mask) * 4  # *4 undoes the half-res downscale
        motion = frames_seen > WARMUP_FRAMES and motion_pixels > min_motion_pixels

        # --- state machine ---
        if writer is None:
            preroll.append(frame)
            if motion:
                ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                clip_path = os.path.join(WORK_DIR, f"otter_{ts}.mp4")
                writer = cv2.VideoWriter(clip_path, FOURCC, FPS, (WIDTH, HEIGHT))
                for f in preroll:  # flush the pre-roll into the clip
                    writer.write(f)
                preroll.clear()
                clip_start = now
                last_motion = now
                print(f"[clip] motion -> recording {os.path.basename(clip_path)}", flush=True)
        else:
            writer.write(frame)
            if motion:
                last_motion = now
            quiet_for = now - last_motion
            too_long = now - clip_start > MAX_CLIP_SECONDS
            if quiet_for > TAIL_SECONDS or too_long:
                reason = "max length" if too_long else "quiet"
                print(f"[clip] stopping ({reason})", flush=True)
                close_clip()

        # --- optional live preview (local testing) ---
        if PREVIEW:
            view = frame.copy()
            recording = writer is not None
            color = (0, 0, 255) if recording else (0, 200, 0)
            label = "REC" if recording else ("MOTION" if motion else "idle")
            cv2.putText(view, f"{label}  motion_px={motion_pixels}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.imshow("otter recorder (q=quit)", view)
            cv2.imshow("motion mask", mask)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                close_clip()
                break

    if process is not None:
        process.kill()
    if PREVIEW:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
