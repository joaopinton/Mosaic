import os
import json

WORKSPACE_DIR = r"e:\padova moto days 2026"
MANIFEST_PATH = os.path.join(WORKSPACE_DIR, "manifest.json")

def rollback():
    if not os.path.exists(MANIFEST_PATH):
        print(f"Errore: {MANIFEST_PATH} non trovato!")
        return

    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    restored = 0
    errors = 0
    for item in manifest:
        orig_name = item.get("original_name")
        curr_name = item.get("current_name") or orig_name
        
        orig_path = os.path.join(WORKSPACE_DIR, orig_name)
        curr_path = os.path.join(WORKSPACE_DIR, curr_name)
        
        if os.path.exists(curr_path) and curr_name != orig_name:
            try:
                os.rename(curr_path, orig_path)
                restored += 1
                item["current_name"] = orig_name
            except Exception as e:
                print(f"Errore nel ripristinare {curr_name} -> {orig_name}: {e}")
                errors += 1
                
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        
    print(f"Rollback completato. File ripristinati: {restored}, Errori: {errors}.")

if __name__ == "__main__":
    rollback()
