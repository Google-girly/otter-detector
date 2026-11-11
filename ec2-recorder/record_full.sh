#!/bin/zsh
# Record the otter cam continuously (no motion gating) to OUT_DIR as 30-minute
# MP4 chunks. Stream-copies (no re-encode) so CPU stays near zero. Restarts if
# the stream drops; exits at STOP_AT (Pacific, HHMM) when the cam goes offline.
#
#   ./record_full.sh                      # -> ~/Desktop/otterclips, 720p, stop 19:05
#   HEIGHT=1080 STOP_AT=1830 ./record_full.sh
set -u
export TZ=America/Los_Angeles
URL="${YOUTUBE_URL:-https://www.youtube.com/watch?v=abbR-Ttd-cA}"
OUT_DIR="${OUT_DIR:-$HOME/Desktop/otterclips}"
HEIGHT="${HEIGHT:-720}"
SEGMENT_SECONDS="${SEGMENT_SECONDS:-1800}"
STOP_AT="${STOP_AT:-1905}"
YTDLP="${YTDLP:-$(dirname "$0")/../.venv/bin/yt-dlp}"

mkdir -p "$OUT_DIR"
echo "[full] recording $URL -> $OUT_DIR (${HEIGHT}p, ${SEGMENT_SECONDS}s chunks, until $STOP_AT PT)"

while [ "$(date +%H%M)" -lt "$STOP_AT" ]; do
  stream=$("$YTDLP" -q --no-warnings -g -f "bestvideo[height<=$HEIGHT]/best[height<=$HEIGHT]/best" "$URL" 2>&1 | tail -1)
  if [[ "$stream" != http* ]]; then
    echo "[full] $(date +%H:%M) could not resolve stream: $stream; retrying in 30s"
    sleep 30; continue
  fi
  echo "[full] $(date +%H:%M) ffmpeg started"
  # Stop ffmpeg cleanly (SIGINT) at STOP_AT so the last chunk is finalized.
  secs_left=$(( $(date -j -f %H%M "$STOP_AT" +%s) - $(date +%s) ))
  ffmpeg -hide_banner -loglevel error -nostdin \
    -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10 \
    -i "$stream" -t "$secs_left" -map 0:v -c copy \
    -f segment -segment_time "$SEGMENT_SECONDS" -reset_timestamps 1 -strftime 1 \
    -segment_format mp4 -segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof \
    "$OUT_DIR/otters-full-%Y-%m-%d-%H%M%S.mp4"
  echo "[full] $(date +%H:%M) ffmpeg exited ($?)"
  sleep 5
done
echo "[full] reached $STOP_AT PT — done"
ls -lh "$OUT_DIR"
