"""
FaceTracer Diagnostic Script
Run: python diagnose.py
"""
import os, json, sys
from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient
import numpy as np

client = MongoClient(os.getenv("MONGODB_URI"))
# Try get_default_database first, fall back to "facetracer"
try:
    db = client.get_default_database()
except Exception:
    db = client["facetracer"]

print("\n" + "="*60)
print("  FaceTracer Database Diagnostics")
print("="*60)

# ── 1. Missing persons ──
persons = list(db["missing_persons"].find({}))
print(f"\n[1] MISSING PERSONS ({len(persons)} total)")
for p in persons:
    enc = p.get("face_encoding")
    enc_len = 0
    if enc:
        try:
            enc_len = len(json.loads(enc))
        except:
            enc_len = -1
    phone = p.get("contact_phone") or "NO PHONE"
    print(f"    Name: {p.get('name','?'):<20} Status: {p.get('status','?'):<12} "
          f"Encoding dims: {enc_len:<6} Phone: {phone}")

# ── 2. Search histories ──
searches = list(db["search_histories"].find({}).sort("search_date", -1).limit(10))
print(f"\n[2] RECENT SEARCHES (last {len(searches)})")
for s in searches:
    print(f"    Match: {str(s.get('match_found','?')):<6} "
          f"Confidence: {s.get('confidence_score',0):.1f}% "
          f"MatchedID: {str(s.get('matched_person_id','none'))[:20]}")

# ── 3. Face recognition service status ──
print("\n[3] FACE RECOGNITION SERVICE")
sys.path.insert(0, ".")
try:
    from app.services.face_recognition_service import face_recognition_service as frs
    print(f"    DNN Active: {frs.dnn_active}")
    print(f"    DNN Detector: {'OK' if frs.detector else 'None'}")
    print(f"    DNN Recognizer: {'OK' if frs.recognizer else 'None'}")
    print(f"    Fallback Cascade: {'OK' if frs.cascade else 'None'}")
except Exception as e:
    print(f"    ERROR loading service: {e}")

# ── 4. Simulate a match between first two cases ──
print("\n[4] ENCODING COMPATIBILITY TEST")
encoded_persons = [p for p in persons if p.get("face_encoding")]
if len(encoded_persons) >= 2:
    p1, p2 = encoded_persons[0], encoded_persons[1]
    enc1 = json.loads(p1["face_encoding"])
    enc2 = json.loads(p2["face_encoding"])
    print(f"    Person 1: {p1.get('name')} — dims: {len(enc1)}")
    print(f"    Person 2: {p2.get('name')} — dims: {len(enc2)}")
    if len(enc1) != len(enc2):
        print(f"    WARNING: Dimension mismatch! {len(enc1)} vs {len(enc2)}")
        print("    This means some cases were registered with DNN and others with fallback.")
        print("    FIX: Re-register cases so all use the same model.")
    else:
        arr1 = np.array(enc1, dtype=np.float32)
        arr2 = np.array(enc2, dtype=np.float32)
        sim = float(np.dot(arr1, arr2) / (np.linalg.norm(arr1) * np.linalg.norm(arr2)))
        print(f"    Cosine similarity between {p1.get('name')} and {p2.get('name')}: {sim:.4f}")
elif len(encoded_persons) == 1:
    enc1 = json.loads(encoded_persons[0]["face_encoding"])
    arr1 = np.array(enc1, dtype=np.float32)
    # Self-similarity test
    sim = float(np.dot(arr1, arr1) / (np.linalg.norm(arr1)**2))
    print(f"    Only 1 encoded case. Self-similarity: {sim:.4f} (should be 1.0)")
    print(f"    Encoding dims: {len(enc1)}")
else:
    print("    No encoded cases found — face_encoding is None for all cases.")
    print("    FIX: Re-register cases with photos. Check if DNN models downloaded.")

# ── 5. Threshold ──
threshold = float(os.getenv("FACE_MATCH_THRESHOLD", "70.0"))
print(f"\n[5] CURRENT THRESHOLD: {threshold}%")
print(f"    To lower: edit FACE_MATCH_THRESHOLD in .env")

# ── 6. Recent audit logs ──
audits = list(db["audit_logs"].find({}).sort("changed_at", -1).limit(5))
print(f"\n[6] RECENT AUDIT LOGS ({len(audits)} shown)")
if audits:
    for a in audits:
        print(f"    Case: {str(a.get('case_id',''))[:10]} "
              f"{a.get('old_status','?')} -> {a.get('new_status','?')} "
              f"Confidence: {a.get('ai_confidence','?')}%")
else:
    print("    No audit logs yet — no case has been auto-resolved.")
    print("    This confirms the face search never reached the resolve step.")

print("\n" + "="*60)
print("  DIAGNOSIS COMPLETE")
print("="*60 + "\n")
