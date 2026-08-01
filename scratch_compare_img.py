import os, cv2, numpy as np

searches_dir = os.path.join("app", "uploads", "searches")
search_path = os.path.join(searches_dir, "ad286577fd45444d83200f5fcb913418.jpg")

img_search = cv2.imread(search_path)
print("Search img shape:", img_search.shape if img_search is not None else "None")

uploads_dir = os.path.join("app", "uploads")
for f in os.listdir(uploads_dir):
    if not f.endswith(('.jpg', '.jpeg', '.png')):
        continue
    up_path = os.path.join(uploads_dir, f)
    img_up = cv2.imread(up_path)
    if img_up is None:
        continue
    # compare shape or histogram
    if img_up.shape == img_search.shape:
        diff = np.mean(np.abs(img_up.astype(float) - img_search.astype(float)))
        print(f"File {f} HAS SAME SHAPE {img_up.shape}, Pixel Diff = {diff:.2f}")
    else:
        print(f"File {f} shape {img_up.shape}")
