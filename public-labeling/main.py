from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from dotenv import load_dotenv
import os

# Load the .env file so we can use SUPABASE_URL and SUPABASE_KEY
load_dotenv()

# Create the app — this is the thing that listens for requests from the frontend
app = FastAPI()

# This allows the frontend (running on a different port) to talk to the backend
# Without this the browser would block the requests for security reasons
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from anywhere (fine for development)
    allow_methods=["*"],  # Allow GET, POST, etc.
    allow_headers=["*"],  # Allow any headers
)

# Connect to Supabase using our credentials from the .env file
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# This is an endpoint — when the frontend calls /next-image
# it gets back the next image that hasn't been labeled yet
@app.get("/next-image")
def get_next_image():
    # Look in the images table for any row where is_labeled is False
    # Only grab 1 at a time
    response = supabase.table("images").select("*").eq("is_labeled", False).limit(1).execute()
    
    # If nothing comes back, all images have been labeled
    if not response.data:
        return {"message": "All images labeled!"}
    
    # Otherwise return the image record (includes filename, storage_path, id)
    return response.data[0]

# This endpoint receives the bounding boxes the user drew
# and saves them to the database
@app.post("/submit-label")
def submit_label(payload: dict):
    # Pull out the three pieces of info the frontend sends us
    image_id = payload["image_id"]    # Which image was labeled
    boxes = payload["boxes"]          # The bounding box coordinates and labels
    labeled_by = payload["labeled_by"] # Who labeled it (their email)

    # Save the label data to the labels table
    supabase.table("labels").insert({
        "image_id": image_id,
        "boxes": boxes,
        "labeled_by": labeled_by,
    }).execute()

    # Mark the image as labeled in the images table so it won't show up again
    supabase.table("images").update({"is_labeled": True}).eq("id", image_id).execute()

    # Send a confirmation back to the frontend
    return {"message": "Label saved!"}