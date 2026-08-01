import os, json
from dotenv import load_dotenv
load_dotenv()
from pymongo import MongoClient

client = MongoClient(os.getenv("MONGODB_URI"))
try:
    db = client.get_default_database()
except Exception:
    db = client["facetracer"]

persons = list(db["missing_persons"].find({}))
print(f"Total missing persons in DB: {len(persons)}")
for p in persons:
    enc = p.get("face_encoding")
    dims = len(json.loads(enc)) if enc else 0
    print(f"ID: {str(p['_id']):<24} Name: {p.get('name','?'):<15} Status: {p.get('status'):<10} Dims: {dims:<5} Photo: {p.get('photo_path')}")
