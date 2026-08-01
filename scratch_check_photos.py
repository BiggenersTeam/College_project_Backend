import os, json
from app.database import get_db

db = get_db()
persons = list(db["missing_persons"].find({}))
backend_dir = os.path.dirname(os.path.abspath(__file__))

print("=== CHECKING ALL MISSING PERSON PHOTOS ===")
for p in persons:
    photo_rel = p.get("photo_path", "")
    clean_rel = photo_rel.lstrip("/")
    
    # Try various relative path resolutions inside FaceTracer_Backend
    possible_paths = [
        os.path.join(backend_dir, "app", clean_rel),
        os.path.join(backend_dir, clean_rel),
        os.path.join(backend_dir, "app", "uploads", os.path.basename(clean_rel)),
        os.path.join(backend_dir, "uploads", os.path.basename(clean_rel)),
    ]
    
    found_path = None
    for path in possible_paths:
        if os.path.exists(path):
            found_path = path
            break
            
    enc_str = p.get("face_encoding")
    enc = json.loads(enc_str) if enc_str else None
    enc_len = len(enc) if enc else 0
    
    print(f"Name: {p.get('name'):<15} | Status: {p.get('status'):<10} | Enc Len: {enc_len:<4} | Found: {found_path is not None} | Path: {found_path or photo_rel}")
