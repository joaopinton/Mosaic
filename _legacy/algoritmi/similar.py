import argparse
import json
import os
import sqlite3
import sys

# Force utf-8 stdout for Windows consoles
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKSPACE_DIR, "padova_moto_days.db")
INDEX_PATH = os.path.join(WORKSPACE_DIR, "similarity_index.json")

def search_photos(query):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    q = f"%{query}%"
    cursor.execute("""
    SELECT id, new_name, brand, model, category, shot_type, primary_colors, relative_path, absolute_path
    FROM photos
    WHERE new_name LIKE ? OR brand LIKE ? OR model LIKE ? OR category LIKE ? OR tags LIKE ?
    ORDER BY id ASC
    """, (q, q, q, q, q))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_similar(photo_query, top_k=5):
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        idx = json.load(f)
        
    matches = [k for k in idx.keys() if photo_query.lower() in k.lower()]
    if not matches:
        print(f"Nessuna foto trovata per il termine '{photo_query}'")
        return
        
    target_key = matches[0]
    print(f"\n=================================================================")
    print(f"[*] FOTO SELEZIONATA: {target_key}")
    print(f"=================================================================\n")
    
    results = idx[target_key][:top_k]
    for i, res in enumerate(results, 1):
        print(f"[{i}] {res['target_name']}")
        print(f"    - Affinita' Vettoriale: {res['score_pct']}%")
        r = res.get('reasons', {})
        print(f"    - Soggetto:     {r.get('soggetto', 'N/D')}")
        print(f"    - Inquadratura: {r.get('inquadratura', 'N/D')}")
        print(f"    - Palette:      {r.get('palette', 'N/D')}")
        print(f"    - Contesto:     {r.get('contesto', 'N/D')}")
        print("-" * 65)

def main():
    parser = argparse.ArgumentParser(description="Padova Moto Days 2026 - Motore di Ricerca & Similarita Vettoriale")
    parser.add_argument("--query", "-q", type=str, help="Cerca foto per parola chiave (es. ferrari, mclaren, vespa, ruota)")
    parser.add_argument("--similar", "-s", type=str, help="Trova le foto piu' simili a un file (es. 0250, 0165, ferrari)")
    parser.add_argument("--top", "-k", type=int, default=5, help="Numero di risultati simili da mostrare")
    args = parser.parse_args()

    if args.similar:
        get_similar(args.similar, args.top)
    elif args.query:
        results = search_photos(args.query)
        print(f"\nTrovate {len(results)} foto per '{args.query}':\n")
        for r in results:
            print(f"- [{r['id']}] {r['new_name']}")
            print(f"    Categoria: {r['category']} | Marca: {r['brand']} {r['model']}")
            print(f"    Path: {r['absolute_path']}\n")
    else:
        print("Usa: python similar.py --similar 0250")
        print("     python similar.py --query ferrari")

if __name__ == "__main__":
    main()
