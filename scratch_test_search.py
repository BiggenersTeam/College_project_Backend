import os, json, cv2
import numpy as np
from app.database import get_db
from app.services.face_recognition_service import face_recognition_service as frs

db = get_db()
persons = list(db["missing_persons"].find({}))
search_path = r'D:\FaceTracer\FaceTracer_Backend\app\uploads\searches\c988913214d74f19930b1f65ba96c2ef.jpeg'

q_enc = frs.get_face_encoding(search_path)
print(f"Query encoding extracted: len {len(q_enc)}")

print("\n=== COMPARING QUERY PHOTO DIRECTLY WITH ALL MISSING PERSON PHOTOS ON DISK ===")
for p in persons:
    photo_rel = p.get("photo_path", "")
    clean_rel = photo_rel.lstrip("/")
    abs_path = os.path.join(r'D:\FaceTracer\FaceTracer_Backend\app', clean_rel)
    if not os.path.exists(abs_path):
        abs_path = os.path.join(r'D:\FaceTracer\FaceTracer_Backend', clean_rel)
    
    if os.path.exists(abs_path):
        # Extract fresh encoding directly from photo on disk using current YuNet/SFace
        fresh_enc = frs.get_face_encoding(abs_path)
        if fresh_enc:
            sim, conf = frs.match_faces(q_enc, fresh_enc)
            stored_enc_len = len(json.loads(p['face_encoding'])) if p.get('face_encoding') else 0
            print(f"Name: {p.get('name'):<15} | Status: {p.get('status'):<10} | StoredEncLen: {stored_enc_len:<4} | FreshEncLen: {len(fresh_enc):<4} | Direct Sim: {sim:.4f} | Conf: {conf:.2f}%")
