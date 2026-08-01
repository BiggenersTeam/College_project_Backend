"""
One-time fix: resolve all cases that were already matched but not updated.
Run: python fix_resolve.py
"""
from dotenv import load_dotenv
load_dotenv()
import os, datetime
from pymongo import MongoClient
from bson import ObjectId

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["facetracer"]

print("\n=== FaceTracer Case Resolution Fix ===\n")

# Find all search histories where match_found=True
matched_searches = list(db["search_histories"].find({"match_found": True}))
print(f"Found {len(matched_searches)} successful face matches in search history")

resolved_count = 0
skipped_count  = 0

for search in matched_searches:
    matched_person_id = search.get("matched_person_id")
    if not matched_person_id:
        continue

    try:
        person = db["missing_persons"].find_one({"_id": ObjectId(matched_person_id)})
    except Exception as e:
        print(f"  ERROR: invalid ID {matched_person_id}: {e}")
        continue

    if not person:
        print(f"  SKIP: person {matched_person_id} not found")
        continue

    current_status = person.get("status", "Active")
    already_resolved = current_status.lower() in {"resolved","solved","found","closed"}

    if already_resolved:
        skipped_count += 1
        continue

    now = datetime.datetime.utcnow()
    confidence = search.get("confidence_score", 0.0)
    query_image = search.get("searched_photo_path", "")
    matched_image = search.get("matched_image_path", "")

    # Update missing_persons → Resolved
    db["missing_persons"].update_one(
        {"_id": ObjectId(matched_person_id)},
        {"$set": {
            "status":             "Resolved",
            "resolved":           True,
            "resolved_at":        now,
            "matched_at":         now,
            "matched_confidence": round(confidence, 2),
            "matched_image_url":  matched_image,
            "matched_query_image": query_image,
            "resolved_by":        "FaceTracer AI",
            "resolution_method":  "AI Face Recognition",
            "last_updated":       now,
        }}
    )

    # Create match_history record (avoid duplicate)
    existing = db["match_histories"].find_one({"case_id": matched_person_id})
    if not existing:
        db["match_histories"].insert_one({
            "case_id":           matched_person_id,
            "matched_person_id": matched_person_id,
            "confidence_score":  round(confidence, 2),
            "query_image":       query_image,
            "matched_image":     matched_image,
            "matched_at":        now,
            "resolved_by":       "FaceTracer AI",
            "resolution_method": "AI Face Recognition",
        })

    # Create audit_log record (avoid duplicate)
    existing_audit = db["audit_logs"].find_one({
        "case_id":    matched_person_id,
        "new_status": "Resolved"
    })
    if not existing_audit:
        db["audit_logs"].insert_one({
            "case_id":       matched_person_id,
            "old_status":    current_status,
            "new_status":    "Resolved",
            "changed_at":    now,
            "changed_by":    "FaceTracer AI",
            "ai_confidence": round(confidence, 2),
            "action":        "CASE_RESOLVED_BY_FACE_RECOGNITION",
        })

    resolved_count += 1
    print(f"  RESOLVED: {person.get('name')} (ID: {matched_person_id[:16]}...) "
          f"Confidence: {confidence:.1f}%")

print(f"\n  Fixed:   {resolved_count} cases resolved")
print(f"  Skipped: {skipped_count} cases (already resolved)")

# Verify final state
print("\n=== Final Database State ===")
for status in ["Active", "Tracing", "Resolved", "Closed"]:
    count = db["missing_persons"].count_documents({"status": status})
    print(f"  {status}: {count}")

print("\n=== Fix Complete ===\n")
print("Now restart your backend server for future face searches to auto-resolve.")
print("Command: uvicorn main:app --reload\n")
