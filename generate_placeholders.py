"""
Generate placeholder images for missing person cases where photos don't exist locally.
Creates a colored image with the person's initials.
Run: python generate_placeholders.py
"""
from dotenv import load_dotenv
load_dotenv()
import os
from pymongo import MongoClient
from bson import ObjectId

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["facetracer"]

UPLOAD_DIR = os.path.join("d:/FaceTracer/FaceTracer_Backend/app/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Color palette based on name hash
COLORS = [
    (11, 61, 145),   # Navy blue
    (19, 136, 8),    # Green
    (220, 38, 38),   # Red
    (217, 119, 6),   # Amber
    (124, 58, 237),  # Purple
    (6, 148, 162),   # Teal
    (239, 68, 68),   # Rose
    (16, 185, 129),  # Emerald
]

def get_color(name):
    idx = sum(ord(c) for c in (name or "X")) % len(COLORS)
    return COLORS[idx]

def generate_placeholder_png(name, size=400):
    """Generate a simple PNG with initials using only stdlib (no Pillow needed)."""
    # Use Pillow if available, otherwise create a minimal valid PNG
    try:
        from PIL import Image, ImageDraw, ImageFont
        color = get_color(name)
        img = Image.new("RGB", (size, size), color)
        draw = ImageDraw.Draw(img)
        
        # Get initials
        parts = (name or "?").strip().split()
        initials = "".join(p[0].upper() for p in parts[:2]) if parts else "?"
        
        # Draw circle background
        margin = 40
        draw.ellipse([margin, margin, size-margin, size-margin], 
                     fill=tuple(max(0, c-30) for c in color))
        
        # Draw text
        font_size = size // 3
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        bbox = draw.textbbox((0,0), initials, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2
        y = (size - text_h) // 2
        draw.text((x, y), initials, fill=(255,255,255), font=font)
        
        return img
    except ImportError:
        return None

def create_minimal_jpeg(name, filename):
    """Create a minimal colored JPEG using PIL or fallback to a pre-made small JPEG."""
    img = generate_placeholder_png(name)
    if img:
        save_path = os.path.join(UPLOAD_DIR, filename)
        img.save(save_path, "JPEG", quality=85)
        return True
    return False

fixed = 0
skipped = 0

for person in db["missing_persons"].find():
    photo_path = person.get("photo_path", "")
    if not photo_path:
        continue
    
    full_path = os.path.join("d:/FaceTracer/FaceTracer_Backend/app", photo_path)
    
    if os.path.exists(full_path):
        skipped += 1
        continue
    
    # File doesn't exist — create placeholder
    filename = os.path.basename(photo_path)
    name = person.get("name", "Unknown")
    
    success = create_minimal_jpeg(name, filename)
    
    if success:
        fixed += 1
        print(f"  Created placeholder for: {name} -> {filename}")
    else:
        print(f"  SKIP (PIL not available): {name}")

print(f"\nCreated: {fixed} placeholders")
print(f"Skipped: {skipped} (already exist)")
print("\nNOTE: Install Pillow for colored initials images: pip install Pillow")
print("Without Pillow, no placeholders are created.")
