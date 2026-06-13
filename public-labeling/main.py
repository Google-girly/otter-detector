from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client
from dotenv import load_dotenv
import os
import random
import secrets

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

_bearer = HTTPBearer()

def require_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password or not secrets.compare_digest(credentials.credentials, password):
        raise HTTPException(status_code=401, detail="Unauthorized")

# All routes live under /api so they don't conflict with the frontend on Vercel
router = APIRouter(prefix="/api")

# This is an endpoint — when the frontend calls /api/next-image
# it gets back the next image that hasn't been labeled yet
@router.get("/next-image")
def get_next_image():
    # Look in the images table for any row where is_labeled is False
    # Only grab 1 at a time
    response = supabase.table("images").select("*").eq("is_labeled", False).limit(50).execute()

    # If nothing comes back, all images have been labeled
    if not response.data:
        return {"message": "All images labeled!"}

    return random.choice(response.data)

# This endpoint receives the bounding boxes the user drew
# and saves them to the database
@router.post("/submit-label")
def submit_label(payload: dict):
    # Pull out the pieces of info the frontend sends us
    image_id = payload["image_id"]    # Which image was labeled
    boxes = payload["boxes"]          # The bounding box coordinates and labels
    labeled_by = payload["labeled_by"] # Who labeled it (their email)
    has_otter = len(boxes) > 0        # True if any boxes were drawn

    # Save the label data to the labels table
    supabase.table("labels").insert({
        "image_id": image_id,
        "boxes": boxes,
        "labeled_by": labeled_by,
        "has_otter": has_otter,
    }).execute()

    # Mark the image as labeled in the images table so it won't show up again
    supabase.table("images").update({"is_labeled": True}).eq("id", image_id).execute()

    # Send a confirmation back to the frontend
    return {"message": "Label saved!"}


@router.post("/admin/login")
def admin_login(payload: dict):
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password or not secrets.compare_digest(payload.get("password", ""), password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": password}


@router.get("/admin/stats")
def get_stats(_=Depends(require_admin)):
    total = supabase.table("images").select("id", count="exact").execute().count
    labeled = supabase.table("images").select("id", count="exact").eq("is_labeled", True).execute().count
    return {
        "total": total,
        "labeled": labeled,
        "unlabeled": total - labeled,
        "percent_complete": round((labeled / total) * 100, 1) if total else 0,
    }


@router.put("/admin/images/{image_id}")
def update_label(image_id: str, payload: dict, _=Depends(require_admin)):
    boxes = payload["boxes"]
    supabase.table("labels").delete().eq("image_id", image_id).execute()
    supabase.table("labels").insert({
        "image_id": image_id,
        "boxes": boxes,
        "labeled_by": "admin",
        "has_otter": len(boxes) > 0,
    }).execute()
    supabase.table("images").update({"is_labeled": True}).eq("id", image_id).execute()
    return {"message": "Label updated!"}


@router.get("/admin/images")
def get_images(_=Depends(require_admin)):
    response = supabase.table("images").select("id, filename, storage_path, is_labeled, created_at, labels(boxes)").order("created_at", desc=True).execute()
    return response.data


app.include_router(router)
