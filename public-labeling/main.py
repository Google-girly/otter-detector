from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client
from dotenv import load_dotenv
import os
import random
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

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

# Connect to Supabase. The backend is the only thing that touches the tables
# (RLS is on with no policies), so it uses the service-role key. Never ship that
# key to the browser — the frontend only gets the publishable key, for Auth.
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY"),
)

_bearer = HTTPBearer()
PACIFIC = ZoneInfo("America/Los_Angeles")

def require_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password or not secrets.compare_digest(credentials.credentials, password):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    """Verify a Supabase Auth access token (sent by the frontend) and return the user."""
    try:
        res = supabase.auth.get_user(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Please sign in again")
    if not res or not res.user:
        raise HTTPException(status_code=401, detail="Please sign in again")
    return res.user


def _hours(active_ms) -> float:
    return round((active_ms or 0) / 3_600_000, 2)


def _public_name(full_name: str) -> str:
    """'Jane Q. Doe' -> 'Jane D.' so the public leaderboard doesn't show full names."""
    parts = (full_name or "").split()
    if not parts:
        return "Volunteer"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."

# All routes live under /api so they don't conflict with the frontend on Vercel
router = APIRouter(prefix="/api")

# A random unlabeled image, picked from a batch of 50 so concurrent volunteers
# rarely get the same one.
@router.get("/next-image")
def get_next_image(user=Depends(require_user)):
    response = supabase.table("images").select("*").eq("is_labeled", False).limit(50).execute()
    if not response.data:
        return {"message": "All images labeled!"}
    return random.choice(response.data)


# Save the boxes a volunteer drew and credit them for it.
@router.post("/submit-label")
def submit_label(payload: dict, user=Depends(require_user)):
    image_id = payload["image_id"]
    boxes = payload.get("boxes") or []
    duration_ms = payload.get("duration_ms")
    duration_ms = max(0, int(duration_ms)) if isinstance(duration_ms, (int, float)) else None

    # Claim the image atomically: only flips if nobody labeled it first. This is
    # what stops two volunteers (or one volunteer re-submitting) from both
    # getting credit for the same frame.
    claimed = (
        supabase.table("images").update({"is_labeled": True})
        .eq("id", image_id).eq("is_labeled", False).execute()
    )
    if not claimed.data:
        raise HTTPException(status_code=409, detail="Someone else already labeled this image")

    supabase.table("labels").insert({
        "image_id": image_id,
        "user_id": user.id,
        "labeled_by": user.email,
        "boxes": boxes,
        "has_otter": len(boxes) > 0,
        "duration_ms": duration_ms,
    }).execute()
    return {"message": "Label saved!"}


@router.get("/me")
def get_me(user=Depends(require_user)):
    """The signed-in volunteer's profile and running totals."""
    profile = supabase.table("profiles").select("*").eq("id", user.id).limit(1).execute().data
    totals = supabase.table("volunteer_totals").select("*").eq("user_id", user.id).limit(1).execute().data
    t = totals[0] if totals else {}

    start_of_day = datetime.now(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    today = (
        supabase.table("labels").select("id", count="exact")
        .eq("user_id", user.id).gte("created_at", start_of_day.isoformat()).execute().count
    ) or 0

    labels = t.get("labels") or 0
    rank = None
    if labels:
        ahead = (
            supabase.table("volunteer_totals").select("user_id", count="exact")
            .gt("labels", labels).execute().count
        ) or 0
        rank = ahead + 1

    p = profile[0] if profile else {}
    return {
        "full_name": p.get("full_name") or user.email,
        "email": user.email,
        "organization": p.get("organization"),
        "labels": labels,
        "labels_today": today,
        "labels_with_otters": t.get("labels_with_otters") or 0,
        "hours": _hours(t.get("active_ms")),
        "rank": rank,
        "first_label_at": t.get("first_label_at"),
        "last_label_at": t.get("last_label_at"),
    }


@router.get("/leaderboard")
def leaderboard(user=Depends(require_user)):
    rows = (
        supabase.table("volunteer_totals").select("user_id, full_name, labels")
        .gt("labels", 0).order("labels", desc=True).limit(10).execute().data
    )
    return [
        {"rank": i + 1, "name": _public_name(r["full_name"]), "labels": r["labels"], "is_me": r["user_id"] == user.id}
        for i, r in enumerate(rows)
    ]


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
    # Correct the boxes in place so the volunteer who labeled it keeps the credit.
    updated = (
        supabase.table("labels").update({"boxes": boxes, "has_otter": len(boxes) > 0})
        .eq("image_id", image_id).execute()
    )
    if not updated.data:
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


@router.get("/admin/volunteers")
def get_volunteers(_=Depends(require_admin)):
    """Every volunteer with their totals — the source for community-service sign-off."""
    rows = supabase.table("volunteer_totals").select("*").order("labels", desc=True).execute().data
    for r in rows:
        r["hours"] = _hours(r.pop("active_ms", 0))
    return rows


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
