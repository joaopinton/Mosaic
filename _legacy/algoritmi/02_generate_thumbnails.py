import os
from PIL import Image
from concurrent.futures import ThreadPoolExecutor

WORKSPACE_DIR = r"e:\padova moto days 2026"
THUMBS_DIR = os.path.join(WORKSPACE_DIR, "thumbnails")

def make_thumbnail(filename):
    src_path = os.path.join(WORKSPACE_DIR, filename)
    dst_path = os.path.join(THUMBS_DIR, filename)
    
    if os.path.exists(dst_path):
        return True
        
    try:
        with Image.open(src_path) as img:
            # Preserve aspect ratio, max 800px
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
            # Convert RGBA to RGB if saving as JPEG
            if img.mode in ('RGBA', 'P') and filename.lower().endswith(('.jpg', '.jpeg')):
                img = img.convert('RGB')
            img.save(dst_path, quality=85, optimize=True)
            return True
    except Exception as e:
        print(f"Errore su {filename}: {e}")
        return False

def main():
    os.makedirs(THUMBS_DIR, exist_ok=True)
    image_exts = ('.jpg', '.jpeg', '.png', '.webp')
    files = [f for f in sorted(os.listdir(WORKSPACE_DIR)) if f.lower().endswith(image_exts) and os.path.isfile(os.path.join(WORKSPACE_DIR, f))]
    
    print(f"Generazione miniature per {len(files)} immagini...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(make_thumbnail, files))
        
    print(f"Completato! {sum(results)}/{len(files)} miniature create in {THUMBS_DIR}.")

if __name__ == "__main__":
    main()
