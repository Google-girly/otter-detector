"""One-time setup: create the private `clips` Storage bucket and check the
`clips` table exists. Reads SUPABASE_URL / SUPABASE_SERVICE_KEY from
public-labeling/.env.

    ../.venv/bin/python setup_supabase.py
"""
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public-labeling", ".env"))
url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
if not url or not key:
    sys.exit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in public-labeling/.env")
bucket = os.getenv("SUPABASE_BUCKET", "clips")
sb = create_client(url, key)

existing = {b.name for b in sb.storage.list_buckets()}
if bucket in existing:
    print(f"bucket '{bucket}' already exists")
else:
    # 50 MB is the Free-tier per-file ceiling; the recorder's settings keep clips well under it.
    sb.storage.create_bucket(bucket, options={
        "public": False,
        "file_size_limit": 50 * 1024 * 1024,
        "allowed_mime_types": ["video/mp4"],
    })
    print(f"created private bucket '{bucket}'")

try:
    sb.table("clips").select("id").limit(1).execute()
    print("table 'clips' OK")
except Exception as e:  # noqa: BLE001
    print(f"table 'clips' missing — run public-labeling/db/clips_schema.sql in the SQL editor ({e})")
