from supabase import create_client
from dotenv import load_dotenv
import os
import time

load_dotenv()

# Service role key required to bypass RLS for storage uploads — never expose this in the frontend
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

FRAMES_DIR = r"C:\Users\desir\OneDrive\Desktop\otter_frames_subset"
BUCKET = "frames"

# Fetch already-uploaded filenames so we can skip them on resume
print("Checking what's already uploaded...")
already_uploaded = set()
page = 0
while True:
    batch = supabase.storage.from_(BUCKET).list(options={"limit": 1000, "offset": page * 1000})
    if not batch:
        break
    already_uploaded.update(f["name"] for f in batch)
    if len(batch) < 1000:
        break
    page += 1
print(f"{len(already_uploaded)} files already in storage, skipping those.")

files = sorted(os.listdir(FRAMES_DIR))
pending = [f for f in files if f not in already_uploaded]
total = len(pending)
print(f"{total} files to upload.")

uploaded = 0
for filename in pending:
    filepath = os.path.join(FRAMES_DIR, filename)
    for attempt in range(3):
        try:
            with open(filepath, "rb") as f:
                supabase.storage.from_(BUCKET).upload(filename, f, {"content-type": "image/jpeg"})
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"FAILED after 3 attempts: {filename} — {e}")
    uploaded += 1
    if uploaded % 50 == 0 or uploaded == total:
        print(f"{uploaded}/{total} uploaded")

print("Done!")
