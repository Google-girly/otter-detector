from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# List all files in the otter_frames subfolder
response = supabase.storage.from_("frames").list("otter_frames")

# Insert a record for each image
for file in response:
    filename = file["name"]
    storage_path = f"frames/otter_frames/{filename}"
    
    supabase.table("images").insert({
        "filename": filename,
        "storage_path": storage_path,
    }).execute()

print(f"Done! Inserted {len(response)} images into the database.")