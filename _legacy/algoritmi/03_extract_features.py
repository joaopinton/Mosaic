import os
import json
import sqlite3
import numpy as np
import onnxruntime as ort
from PIL import Image

WORKSPACE_DIR = r"e:\padova moto days 2026"
THUMBS_DIR = os.path.join(WORKSPACE_DIR, "thumbnails")
ONNX_MODEL = os.path.join(WORKSPACE_DIR, "mobilenetv2-7.onnx")

def get_dominant_colors(pil_img, k=3):
    img = pil_img.copy().resize((64, 64))
    # reduce to palette
    result = img.convert('P', palette=Image.Palette.ADAPTIVE, colors=k)
    palette = result.getpalette()[:k*3]
    color_counts = sorted(result.getcolors(), reverse=True)
    
    named_colors = []
    rgb_colors = []
    
    def rgb_to_name(r, g, b):
        # simple classification
        brightness = (r*299 + g*587 + b*114) / 1000
        if brightness < 35:
            return "Nero"
        if brightness > 220 and max(abs(r-g), abs(r-b), abs(g-b)) < 25:
            return "Bianco"
        if max(abs(r-g), abs(r-b), abs(g-b)) < 25:
            return "Grigio"
        if r > 150 and g < 70 and b < 70:
            return "Rosso"
        if r > 180 and g > 100 and b < 50:
            return "Arancione"
        if r > 180 and g > 180 and b < 60:
            return "Giallo"
        if g > r + 30 and g > b + 30:
            return "Verde"
        if b > r + 30 and b > g + 30:
            return "Blu"
        return "Altro"

    for count, idx in color_counts[:k]:
        r = palette[idx*3]
        g = palette[idx*3 + 1]
        b = palette[idx*3 + 2]
        cname = rgb_to_name(r, g, b)
        if cname not in named_colors and cname != "Altro":
            named_colors.append(cname)
        rgb_colors.append((r, g, b))
        
    if not named_colors:
        named_colors = ["Multicolore"]
    return named_colors, rgb_colors

def main():
    session = ort.InferenceSession(ONNX_MODEL)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    
    with open(os.path.join(WORKSPACE_DIR, 'manifest.json'), 'r', encoding='utf-8') as f:
        manifest = json.load(f)
        
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    
    features_cache = {}
    print(f"Estrazione feature vettoriali ONNX per {len(manifest)} immagini...")
    
    for idx, item in enumerate(manifest):
        fname = item['original_name']
        thumb_path = os.path.join(THUMBS_DIR, fname)
        if not os.path.exists(thumb_path):
            continue
            
        img = Image.open(thumb_path).convert('RGB')
        named_colors, rgb_colors = get_dominant_colors(img)
        
        # ONNX input
        img_224 = img.resize((224, 224))
        arr = (np.array(img_224).astype(np.float32) / 255.0 - mean) / std
        arr = np.transpose(arr, (2, 0, 1))[np.newaxis, :]
        
        logits = session.run([output_name], {input_name: arr})[0][0]
        # Softmax for probability distribution
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)
        
        # Save L2 normalized vector
        vec = probs / np.linalg.norm(probs)
        
        features_cache[fname] = {
            "colors": named_colors,
            "vector": vec.tolist(),
            "top_classes": np.argsort(logits)[-5:][::-1].tolist()
        }
        
        if (idx + 1) % 50 == 0 or idx == len(manifest) - 1:
            print(f"Completate {idx + 1}/{len(manifest)} immagini...")
            
    with open(os.path.join(WORKSPACE_DIR, 'features_cache.json'), 'w', encoding='utf-8') as f:
        json.dump(features_cache, f)
        
    print("Estrazione feature completata con successo!")

if __name__ == "__main__":
    main()
