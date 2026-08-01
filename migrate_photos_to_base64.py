"""
Migrate existing MongoDB photo_path values to Base64 data URIs.
Run once:  python migrate_photos_to_base64.py
"""
import sys, io, os, base64, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv; load_dotenv()
from pymongo import MongoClient

client = MongoClient(os.getenv('MONGODB_URI'))
db     = client['facetracer']

def to_base64(path_abs: str) -> str:
    ext  = (os.path.splitext(path_abs)[1] or '.jpg').lower()
    mime = {'.jpg':'image/jpeg','.jpeg':'image/jpeg',
            '.png':'image/png','.webp':'image/webp',
            '.gif':'image/gif'}.get(ext, 'image/jpeg')
    with open(path_abs, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    return f"data:{mime};base64,{b64}"

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
APP_UPLOADS = os.path.join(SCRIPT_DIR, 'app', 'uploads')

# Build a map of all local files: basename → absolute path
local_files = {}
for f in glob.glob(os.path.join(APP_UPLOADS, '**', '*.*'), recursive=True):
    local_files[os.path.basename(f)] = f

print(f"Local files found in app/uploads: {len(local_files)}")
for fname in local_files:
    print(f"  {fname}")
print()

records = list(db['missing_persons'].find(
    {'photo_path': {'$not': {'$regex': '^data:'}}},
    {'_id': 1, 'name': 1, 'photo_path': 1}
))
print(f"DB records to migrate: {len(records)}")
print()

converted = 0
skipped   = 0

for r in records:
    path = r.get('photo_path', '')
    name = r.get('name', '?')

    if path.startswith('http://') or path.startswith('https://'):
        print(f"  SKIP (https) : {name}")
        continue

    # Try to find the file by basename
    basename  = os.path.basename(path.lstrip('/'))
    local_abs = local_files.get(basename)

    if not local_abs:
        print(f"  MISSING : {name} — {path}")
        skipped += 1
        continue

    try:
        b64 = to_base64(local_abs)
        db['missing_persons'].update_one(
            {'_id': r['_id']},
            {'$set': {'photo_path': b64}}
        )
        print(f"  CONVERTED : {name} ({len(b64)//1024} KB base64)")
        converted += 1
    except Exception as e:
        print(f"  ERROR : {name} — {e}")
        skipped += 1

print()
print(f"Done. Converted: {converted} | Missing: {skipped}")
print("Records showing MISSING need to be re-uploaded from the website.")
