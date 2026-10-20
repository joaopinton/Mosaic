import sqlite3
import json
import os
import numpy as np

WORKSPACE_DIR = r"e:\padova moto days 2026"
DB_PATH = os.path.join(WORKSPACE_DIR, "padova_moto_days.db")
INDEX_PATH = os.path.join(WORKSPACE_DIR, "similarity_index.json")

def cosine_similarity(v1, v2):
    dot = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot / (norm1 * norm2))

def explain_similarity(photo_a, photo_b, score):
    reasons = {}
    
    # 1. Soggetto
    brand_a, brand_b = photo_a.get("brand", ""), photo_b.get("brand", "")
    cat_a, cat_b = photo_a.get("category", ""), photo_b.get("category", "")
    mod_a, mod_b = photo_a.get("model", ""), photo_b.get("model", "")
    
    if brand_a and brand_b and brand_a == brand_b:
        reasons["soggetto"] = f"Stesso marchio costruttore: {brand_a}"
        if mod_a and mod_b and mod_a == mod_b:
            reasons["soggetto"] += f" (entrambe modello {mod_a})"
    elif cat_a == cat_b:
        reasons["soggetto"] = f"Stessa tipologia di veicolo / esposizione: {cat_a}"
    else:
        reasons["soggetto"] = f"Tematica affine ({cat_a} vs {cat_b})"

    # 2. Inquadratura
    shot_a, shot_b = photo_a.get("shot_type", ""), photo_b.get("shot_type", "")
    if shot_a == shot_b:
        if "Macro" in shot_a or "Ruota" in shot_a:
            reasons["inquadratura"] = "Entrambe macro ravvicinate focalizzate su gruppo ruota, disco e pinza freno"
        elif "3/4" in shot_a:
            reasons["inquadratura"] = "Identica prospettiva dinamica con inquadratura 3/4"
        elif "Palco" in shot_a or "Consegna" in shot_a:
            reasons["inquadratura"] = "Stessa posa ufficiale a figura intera sul palco"
        else:
            reasons["inquadratura"] = f"Stesso schema compositivo: {shot_a}"
    else:
        reasons["inquadratura"] = f"Inquadrature complementari ({shot_a} e {shot_b})"

    # 3. Palette Colori
    colors_a = set([c.strip().lower() for c in photo_a.get("primary_colors", "").split(",") if c.strip()])
    colors_b = set([c.strip().lower() for c in photo_b.get("primary_colors", "").split(",") if c.strip()])
    common_colors = colors_a.intersection(colors_b)
    if common_colors:
        color_str = ", ".join(sorted(list(common_colors))).capitalize()
        reasons["palette"] = f"Tonalità dominanti condivise: {color_str}"
    else:
        reasons["palette"] = "Contrasti cromatici e texture visive ad alta affinità"

    # 4. Contesto
    desc_a, desc_b = photo_a.get("description", "").lower(), photo_b.get("description", "").lower()
    if "cortile" in desc_a and "cortile" in desc_b or "piazzale" in desc_a and "piazzale" in desc_b:
        reasons["contesto"] = "Scattate entrambe all'aperto durante il raduno supercar nel cortile"
    elif "palco" in desc_a and "palco" in desc_b or "attestato" in desc_a and "attestato" in desc_b:
        reasons["contesto"] = "Momento istituzionale di gala serale davanti al logo ufficiale"
    elif "stand" in desc_a and "stand" in desc_b:
        reasons["contesto"] = "Allestimento espositivo al coperto nei padiglioni della fiera"
    elif "inaugurazione" in desc_a and "inaugurazione" in desc_b:
        reasons["contesto"] = "Cerimonia ufficiale di apertura con le autorità"
    else:
        reasons["contesto"] = "Ambiente fiera Padova Moto Days 2026 (Piazzola sul Brenta)"

    return reasons

def compute_all_similarities():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, original_name, new_name, category, brand, model, shot_type, primary_colors, description, tags, embedding FROM photos ORDER BY id ASC")
    photos = [dict(r) for r in cursor.fetchall()]
    
    print(f"Calcolo matrice di similarità per {len(photos)} foto...")
    
    # Parse embeddings
    for p in photos:
        p["vec"] = np.array(json.loads(p["embedding"]), dtype=np.float32)
        # remove big text
        del p["embedding"]
        
    similarity_index = {}
    
    cursor.execute("DELETE FROM similarities;")
    
    for i, p_i in enumerate(photos):
        scores = []
        vec_i = p_i["vec"]
        
        for j, p_j in enumerate(photos):
            if i == j:
                continue
            vec_j = p_j["vec"]
            
            # Base visual similarity
            cos_sim = cosine_similarity(vec_i, vec_j)
            
            # Boosts for structural alignment
            boost = 0.0
            if p_i["brand"] and p_i["brand"] == p_j["brand"]:
                boost += 0.18
            if p_i["category"] == p_j["category"]:
                boost += 0.14
            if p_i["shot_type"] == p_j["shot_type"]:
                boost += 0.12
                
            # Colors intersection
            colors_i = set([c.strip().lower() for c in p_i.get("primary_colors", "").split(",") if c.strip()])
            colors_j = set([c.strip().lower() for c in p_j.get("primary_colors", "").split(",") if c.strip()])
            if colors_i.intersection(colors_j):
                boost += 0.08
                
            final_score = min(1.0, max(0.0, cos_sim * 0.48 + boost))
            scores.append((final_score, j))
            
        scores.sort(key=lambda x: x[0], reverse=True)
        top6 = scores[:6]
        
        sim_list = []
        for score, j_idx in top6:
            p_j = photos[j_idx]
            reasons = explain_similarity(p_i, p_j, score)
            
            sim_list.append({
                "target_id": p_j["id"],
                "target_name": p_j["new_name"],
                "target_brand": p_j["brand"],
                "target_category": p_j["category"],
                "score_pct": round(score * 100, 1),
                "reasons": reasons
            })
            
            # Record in SQLite
            cursor.execute("""
            INSERT OR REPLACE INTO similarities (photo_id_a, photo_id_b, score, reasons)
            VALUES (?, ?, ?, ?)
            """, (p_i["id"], p_j["id"], score, json.dumps(reasons, ensure_ascii=False)))
            
        similarity_index[p_i["new_name"]] = sim_list
        
        if (i + 1) % 50 == 0 or i == len(photos) - 1:
            print(f"Elaborate similarità {i + 1}/{len(photos)} foto...")
            
    conn.commit()
    conn.close()
    
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(similarity_index, f, indent=2, ensure_ascii=False)
        
    print(f"Indice di similarità salvato in {INDEX_PATH} e nel database SQLite!")

if __name__ == "__main__":
    compute_all_similarities()
