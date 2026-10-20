import os
import json
import hashlib
from PIL import Image
from PIL.ExifTags import TAGS

WORKSPACE_DIR = r"e:\padova moto days 2026"
MANIFEST_PATH = os.path.join(WORKSPACE_DIR, "manifest.json")

def get_file_hash(filepath, block_size=65536):
    sha = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            sha.update(block)
    return sha.hexdigest()

def extract_exif(filepath):
    metadata = {}
    try:
        with Image.open(filepath) as img:
            metadata['width'] = img.width
            metadata['height'] = img.height
            metadata['format'] = img.format
            metadata['mode'] = img.mode
            
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    # convert un-serializable types
                    if isinstance(value, (bytes, bytearray)):
                        continue
                    if isinstance(value, (str, int, float)):
                        metadata[str(tag_name)] = value
                    else:
                        metadata[str(tag_name)] = str(value)
    except Exception as e:
        metadata['error'] = str(e)
    return metadata

def main():
    files = [f for f in os.listdir(WORKSPACE_DIR) if os.path.isfile(os.path.join(WORKSPACE_DIR, f))]
    # Filter only image files
    image_exts = {'.jpg', '.jpeg', '.png', '.webp'}
    image_files = [f for f in sorted(files) if os.path.splitext(f)[1].lower() in image_exts]
    
    print(f"Trovate {len(image_files)} immagini.")
    manifest = []
    
    for idx, fname in enumerate(image_files):
        fpath = os.path.join(WORKSPACE_DIR, fname)
        file_size = os.path.getsize(fpath)
        exif_data = extract_exif(fpath)
        file_hash = get_file_hash(fpath)
        
        manifest.append({
            "index": idx + 1,
            "original_name": fname,
            "original_path": fpath,
            "relative_path": fname,
            "sha256": file_hash,
            "size_bytes": file_size,
            "width": exif_data.get("width"),
            "height": exif_data.get("height"),
            "exif": exif_data
        })
        if (idx + 1) % 50 == 0 or idx == len(image_files) - 1:
            print(f"Processate {idx + 1}/{len(image_files)} foto per il manifest...")
            
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        
    print(f"Manifest salvato con successo in {MANIFEST_PATH} ({len(manifest)} elementi).")

if __name__ == "__main__":
    main()
