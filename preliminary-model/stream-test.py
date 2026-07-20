import yt_dlp
import cv2
import numpy as np
import subprocess

YOUTUBE_URL = 'https://www.youtube.com/watch?v=abbR-Ttd-cA'
WIDTH, HEIGHT = 854, 480
FRAME_SIZE = WIDTH * HEIGHT * 3

def get_stream_url():
    # re-extracts a fresh URL each time — YouTube URLs expire after a few minutes
    ydl_opts = {
        'quiet': True,
        'format': 'best[height<=480]/best',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(YOUTUBE_URL, download=False)
        return info['url']

def start_ffmpeg(url):
    # starts ffmpeg as a subprocess, piping raw BGR frames to stdout
    cmd = [
        'ffmpeg',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', url,
        '-f', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-vf', f'scale={WIDTH}:{HEIGHT}',
        '-an',
        '-r', '30',  # cap output at 30fps to match the stream's actual rate
        'pipe:1',
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)

# ── Main loop ─────────────────────────────────────────────────────────────────
# if ffmpeg dies (expired URL, network drop, etc.) we fetch a fresh URL and restart it

process = None

while True:
    # start or restart ffmpeg with a fresh URL
    if process is None or process.poll() is not None:
        print("(Re)connecting to stream...")
        url = get_stream_url()
        if process is not None:
            process.terminate()
        process = start_ffmpeg(url)

    raw = process.stdout.read(FRAME_SIZE)

    # if we didn't get a full frame, ffmpeg died — loop will restart it
    if len(raw) != FRAME_SIZE:
        process = None
        continue

    frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))
    cv2.imshow('Stream Test', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

if process:
    process.terminate()
cv2.destroyAllWindows()
