"""
Restore real photos from Render server.
Deletes fake placeholder images and re-downloads originals.
Run: python restore_photos.py
"""
import requests, os, sys
from dotenv import load_dotenv
load_dotenv()
from pymongo import MongoClient
from PIL import Image
import io

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["facetracer"]

RENDER_URL = "https://facetracer-backend.onrender.com"
LOCAL_BASE  = "d:/FaceTracer/FaceTracer_Backend/app"

# Detect fake placeholders — real photos are > 10KB, fake ones are small
MIN_REAL_PHOTO_SIZE = 50000  # bytes — real face photos are typically >50KB

print("=" * 60)
print("  FaceTracer Photo Restoration Tool")
print("=" * 60)

results = {"downloaded": 0, "already_real": 0, "not_on_render": 0, "error": 0}

for person in db["missing_persons"].find():
    photo_path = person.get("photo_path", "")
    name = person.get("name", "Unknown")
    if not photo_path:
        continue

    local_path = os.path.join(LOCAL_BASE, photo_path)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    # Check if existing file is a real photo or fake placeholder
    is_fake = False
    if os.path.exists(local_path):
        size = os.path.getsize(local_path)
        if size < MIN_REAL_PHOTO_SIZE:
            is_fake = True
            print(f"\n  [{name}] Fake placeholder detected ({size} bytes) — replacing...")
        else:
            results["already_real"] += 1
            print(f"  [{name}] OK — real photo exists ({size} bytes)")
            continue

    # Try to download from Render
    url = f"{RENDER_URL}/{photo_path}"
    print(f"\n  [{name}] Downloading from Render...")
    print(f"    URL: {url}")

    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "FaceTracer-Restore/1.0"})

        if resp.status_code == 200 and len(resp.content) > MIN_REAL_PHOTO_SIZE:
            # Verify it's a real image
            try:
                img = Image.open(io.BytesIO(resp.content))
                img.verify()
                # Re-open after verify (verify closes the image)
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                size = len(resp.content)
                print(f"    SUCCESS — {size} bytes, {img.format} {img.size}")
                results["downloaded"] += 1
            except Exception as img_err:
                print(f"    Invalid image data: {img_err}")
                results["error"] += 1

        elif resp.status_code == 404:
            print(f"    Not found on Render (404) — photo was deleted from cloud")
            results["not_on_render"] += 1
            # Keep existing placeholder if it exists, otherwise note it
            if not os.path.exists(local_path) or is_fake:
                print(f"    NOTE: Re-register this case locally to get a real photo")

        else:
            print(f"    HTTP {resp.status_code} — {len(resp.content)} bytes")
            results["not_on_render"] += 1

    except requests.exceptions.Timeout:
        print(f"    TIMEOUT — Render may be sleeping, try again in 30 seconds")
        results["error"] += 1
    except Exception as e:
        print(f"    ERROR: {e}")
        results["error"] += 1

print("\n" + "=" * 60)
print(f"  Downloaded from Render:  {results['downloaded']}")
print(f"  Already real (kept):     {results['already_real']}")
print(f"  Not on Render (deleted): {results['not_on_render']}")
print(f"  Errors:                  {results['error']}")
print("=" * 60)

if results["not_on_render"] > 0:
    print(f"\n  {results['not_on_render']} photo(s) are permanently gone from Render.")
    print("  To restore them: Re-register those cases with the original photos.")
    print("  The case data (name, age, location) is still in MongoDB.")
