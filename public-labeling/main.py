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


# ── Model evaluation (see db/eval_schema.sql + review.html) ──────────────────────
# These power the standalone review page where a human scores otter_v2's
# predictions. Kept separate from the labeling routes above so evaluation never
# touches the crowd training labels.

VALID_VERDICTS = {"correct", "missed", "false_alarm", "box_off", "correctly_empty"}


def _public_url(storage_path: str) -> str:
    """storage_path is 'frames/eval/<name>' -> public URL to the image."""
    return f"{os.getenv('SUPABASE_URL')}/storage/v1/object/public/{storage_path}"


@router.get("/eval/next")
def eval_next():
    """Return the next prediction that has no feedback row yet (i.e. unreviewed)."""
    reviewed = supabase.table("eval_feedback").select("prediction_id").execute()
    reviewed_ids = {r["prediction_id"] for r in reviewed.data}

    # Pull a batch ordered oldest-first and return the first unreviewed one.
    preds = supabase.table("eval_predictions").select("*").order("created_at").limit(500).execute()
    for p in preds.data:
        if p["id"] not in reviewed_ids:
            p["image_url"] = _public_url(p["storage_path"])
            return p
    return {"message": "All predictions reviewed!"}


def _derive_verdict(n_boxes: int, n_correct: int, n_false: int, n_missed: int) -> str:
    """Collapse the per-frame counts into one readable label ('mixed' when a
    frame has more than one kind of outcome)."""
    if n_boxes == 0 and n_missed == 0:
        return "correctly_empty"
    if n_correct > 0 and n_false == 0 and n_missed == 0:
        return "correct"
    if n_correct == 0 and n_false > 0 and n_missed == 0:
        return "false_alarm"
    if n_correct == 0 and n_false == 0 and n_missed > 0:
        return "missed"
    return "mixed"


@router.post("/eval/feedback")
def eval_feedback(payload: dict):
    """Record one human verdict on a prediction, as per-frame detection counts.

    The client sends how many of the model's boxes were correct plus the boxes
    the human drew for missed otters; everything else is derived here so the math
    stays in one place.
    """
    corrected = payload.get("corrected_boxes") or []
    n_boxes = int(payload.get("n_boxes", 0))
    n_correct = max(0, min(int(payload.get("n_correct", 0)), n_boxes))
    n_false = n_boxes - n_correct
    n_missed = len(corrected)
    box_off = bool(payload.get("box_off", False))
    verdict = _derive_verdict(n_boxes, n_correct, n_false, n_missed)

    supabase.table("eval_feedback").insert({
        "prediction_id": payload["prediction_id"],
        "verdict": verdict,
        "reviewed_by": payload.get("reviewed_by") or "anon",
        "notes": payload.get("notes") or None,
        "corrected_boxes": corrected,   # boxes the human drew, original px coords
        "n_boxes": n_boxes,
        "n_correct": n_correct,
        "n_false": n_false,
        "n_missed": n_missed,
        "box_off": box_off,
    }).execute()
    return {"message": "Verdict saved!", "verdict": verdict}


@router.get("/eval/stats")
def eval_stats():
    """Live scoreboard: verdict counts + precision/recall for the write-up.

    Frame-level definitions:
      correct/box_off  -> otter present AND detected  (True Positive)
      missed           -> otter present, not detected (False Negative)
      false_alarm      -> detected but no otter       (False Positive)
      correctly_empty  -> no otter, nothing detected  (True Negative)
    """
    total_preds = supabase.table("eval_predictions").select("id", count="exact").execute().count or 0
    fb = supabase.table("eval_feedback").select("verdict, n_correct, n_false, n_missed, box_off").execute().data

    # Box-level totals -> exact precision/recall, even on multi-otter frames.
    tp = sum(r.get("n_correct") or 0 for r in fb)   # boxes correctly on an otter
    fp = sum(r.get("n_false") or 0 for r in fb)     # boxes on a non-otter
    fn = sum(r.get("n_missed") or 0 for r in fb)    # otters with no box
    box_off_frames = sum(1 for r in fb if r.get("box_off"))

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None

    # Frame-level verdict tally (readable breakdown for the writeup).
    verdicts = {}
    for r in fb:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1

    reviewed = len(fb)
    return {
        "model_reviewed": reviewed,
        "predictions_total": total_preds,
        "remaining": max(total_preds - reviewed, 0),
        "boxes": {"tp": tp, "fp": fp, "fn": fn},
        "verdicts": verdicts,
        "box_off_frames": box_off_frames,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
    }


app.include_router(router)
