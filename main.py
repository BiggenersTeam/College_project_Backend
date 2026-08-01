import os
import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import auth, users, missing_persons, face_search, dashboard, calls, notifications, vapi

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="FaceTracer API",
    description="Backend API for Face Recognition Missing Person Finder System",
    version="2.0.0"
)

@app.on_event("startup")
async def startup_event():
    logger.info("="*55)
    logger.info("  FaceTracer Backend v2.0 — STARTED")
    logger.info("  Auto-resolve: ENABLED")
    logger.info("="*55)
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=5000)
        db = client["facetracer"]
        _auto_resolve_pending(db)
        client.close()
    except Exception as e:
        logger.warning(f"Startup auto-resolve skipped: {e}")


@app.get("/health")
def health_check():
    """Health check + trigger auto-resolve for any missed cases."""
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=5000)
        db = client["facetracer"]
        _auto_resolve_pending(db)
        active   = db["missing_persons"].count_documents({"status": "Active"})
        resolved = db["missing_persons"].count_documents({"status": "Resolved"})
        client.close()
        return {"status": "ok", "active": active, "resolved": resolved, "version": "2.0"}
    except Exception as e:
        return {"status": "ok", "error": str(e)}

@app.get("/ping")
def ping():
    """Keep-alive endpoint — prevents Render free tier from sleeping."""
    return {"status": "alive", "message": "FaceTracer backend is running"}

def _auto_resolve_pending(db):
    """Resolve all cases that have successful search matches but are still Active/Tracing."""
    import json, datetime
    from bson import ObjectId
    resolved_statuses = {"resolved", "solved", "found", "traced", "closed"}
    matched = list(db["search_histories"].find({"match_found": True}))
    count = 0
    for s in matched:
        mid = s.get("matched_person_id")
        if not mid:
            continue
        try:
            person = db["missing_persons"].find_one({"_id": ObjectId(mid)})
        except Exception:
            continue
        if not person:
            continue
        if (person.get("status", "Active") or "Active").lower() in resolved_statuses:
            continue
        now = datetime.datetime.utcnow()
        confidence = s.get("confidence_score", 0.0)
        db["missing_persons"].update_one(
            {"_id": ObjectId(mid)},
            {"$set": {
                "status": "Resolved", "resolved": True,
                "resolved_at": now, "matched_at": now,
                "matched_confidence": round(confidence, 2),
                "matched_query_image": s.get("searched_photo_path", ""),
                "resolved_by": "FaceTracer AI",
                "resolution_method": "AI Face Recognition",
                "last_updated": now,
            }}
        )
        # Create audit log if missing
        existing = db["audit_logs"].find_one({"case_id": str(mid), "new_status": "Resolved"})
        if not existing:
            db["audit_logs"].insert_one({
                "case_id": str(mid),
                "old_status": person.get("status", "Active"),
                "new_status": "Resolved",
                "changed_at": now,
                "changed_by": "FaceTracer AI (startup)",
                "ai_confidence": round(confidence, 2),
                "action": "AUTO_RESOLVED_ON_STARTUP",
            })
        # Dispatch resolution event (AI voice call)
        try:
            from app.services.events import dispatch_case_resolved
            import threading
            fresh_person = db["missing_persons"].find_one({"_id": ObjectId(mid)}) or person
            threading.Thread(
                target=dispatch_case_resolved,
                kwargs={
                    "case": fresh_person,
                    "new_status": "Resolved",
                    "db_uri": os.getenv("MONGODB_URI", ""),
                    "confidence": confidence,
                },
                daemon=True
            ).start()
        except Exception as exc:
            logger.warning(f"  [Startup] Failed to dispatch resolution call for {mid}: {exc}")

        count += 1
        logger.info(f"  [Startup] Auto-resolved: {person.get('name')} | conf: {confidence:.1f}%")
    if count:
        logger.info(f"  [Startup] Total auto-resolved: {count} cases")
    else:
        logger.info("  [Startup] All matched cases already resolved.")

# CORS configuration — allow all origins for easy deployment
# In production, replace "*" with your Vercel frontend domain
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "https://face-tracer-frontend.vercel.app",
    "https://final-frontend.vercel.app",
    "https://final-frontend-git-main-gayatrihippale-arch.vercel.app",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount uploads directory to serve images statically
# Files are saved inside app/uploads/ by the routers — mount that directory
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Include Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(missing_persons.router)
app.include_router(face_search.router)
app.include_router(dashboard.router)
app.include_router(calls.router)
app.include_router(notifications.router)
app.include_router(vapi.router)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Welcome to FaceTracer API. System is fully operational.",
        "version": "1.0.0",
        "database": "MongoDB Atlas"
    }


@app.on_event("startup")
async def startup_db_check():
    """Verify MongoDB connection on startup."""
    try:
        from app.database import client
        client.admin.command("ping")
        print("[OK] Successfully connected to MongoDB Atlas!")
    except Exception as e:
        # Log the error but do NOT raise — crashing here takes down all routes
        print(f"[WARNING] MongoDB connection check failed: {e}")
        print("[INFO] Server will continue — DB connection will be retried on first request.")
