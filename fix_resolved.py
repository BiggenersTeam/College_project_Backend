"""Fix: resolve Shravani and any other unresolved matched cases."""
from dotenv import load_dotenv
load_dotenv()
import os, datetime
from pymongo import MongoClient
from bson import ObjectId

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["facetracer"]

print("=== Current Active Cases ===")
for p in db["missing_persons"].find({"status": "Active"}):
    print(f"  {p.get('name')} | ID: {p['_id']}")

print()
print("=== Recent Successful Matches ===")
for s in db["search_histories"].find({"match_found": True}).sort("search_date", -1).limit(10):
    mid = s.get("matched_person_id")
    conf = s.get("confidence_score", 0)
    if mid:
        person = db["missing_persons"].find_one({"_id": ObjectId(mid)})
        name = person.get("name") if person else "NOT FOUND"
        status = person.get("status") if person else "N/A"
        print(f"  Match: {name} | Status: {status} | Confidence: {conf}%")

print()
print("=== Running fix_resolve ===")
# Run the comprehensive fix
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
    current_status = (person.get("status", "Active") or "Active")
    if current_status.lower() in resolved_statuses:
        continue

    now = datetime.datetime.utcnow()
    confidence = s.get("confidence_score", 0.0)

    db["missing_persons"].update_one(
        {"_id": ObjectId(mid)},
        {"$set": {
            "status": "Resolved",
            "resolved": True,
            "resolved_at": now,
            "matched_at": now,
            "matched_confidence": round(confidence, 2),
            "matched_image_url": s.get("matched_image_path", ""),
            "matched_query_image": s.get("searched_photo_path", ""),
            "resolved_by": "FaceTracer AI",
            "resolution_method": "AI Face Recognition",
            "last_updated": now,
        }}
    )

    existing_audit = db["audit_logs"].find_one({
        "case_id": mid, "new_status": "Resolved"
    })
    if not existing_audit:
        db["audit_logs"].insert_one({
            "case_id": mid,
            "old_status": current_status,
            "new_status": "Resolved",
            "changed_at": now,
            "changed_by": "FaceTracer AI (fix script)",
            "ai_confidence": round(confidence, 2),
            "action": "AUTO_RESOLVED_FIX",
        })

    count += 1
    print(f"  RESOLVED: {person.get('name')} (confidence: {confidence}%)")

print(f"\nFixed: {count} cases")

print("\n=== Final State ===")
for s in ["Active", "Resolved", "Tracing", "Closed"]:
    c = db["missing_persons"].count_documents({"status": s})
    print(f"  {s}: {c}")
