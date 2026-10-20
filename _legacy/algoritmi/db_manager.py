import sqlite3
import json
import os
import csv

DB_PATH = r"e:\padova moto days 2026\padova_moto_days.db"

def get_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=DB_PATH):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_name TEXT UNIQUE NOT NULL,
        new_name TEXT,
        relative_path TEXT NOT NULL,
        absolute_path TEXT NOT NULL,
        category TEXT,
        brand TEXT,
        model TEXT,
        shot_type TEXT,
        primary_colors TEXT,
        description TEXT,
        tags TEXT,
        width INTEGER,
        height INTEGER,
        size_bytes INTEGER,
        sha256 TEXT,
        embedding TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS similarities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id_a INTEGER,
        photo_id_b INTEGER,
        score REAL,
        reasons TEXT,
        FOREIGN KEY (photo_id_a) REFERENCES photos(id),
        FOREIGN KEY (photo_id_b) REFERENCES photos(id),
        UNIQUE(photo_id_a, photo_id_b)
    );
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_category ON photos(category);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_brand ON photos(brand);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_shot_type ON photos(shot_type);")
    
    conn.commit()
    conn.close()

def upsert_photo(data, db_path=DB_PATH):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # Ensure tags and embedding are stored properly
    tags = data.get("tags")
    if isinstance(tags, list):
        tags = ", ".join(tags)
    colors = data.get("primary_colors")
    if isinstance(colors, list):
        colors = ", ".join(colors)
    embedding = data.get("embedding")
    if isinstance(embedding, list):
        embedding = json.dumps(embedding)
        
    cursor.execute("""
    INSERT INTO photos (
        original_name, new_name, relative_path, absolute_path,
        category, brand, model, shot_type, primary_colors,
        description, tags, width, height, size_bytes, sha256, embedding
    ) VALUES (
        :original_name, :new_name, :relative_path, :absolute_path,
        :category, :brand, :model, :shot_type, :primary_colors,
        :description, :tags, :width, :height, :size_bytes, :sha256, :embedding
    ) ON CONFLICT(original_name) DO UPDATE SET
        new_name=excluded.new_name,
        relative_path=excluded.relative_path,
        absolute_path=excluded.absolute_path,
        category=excluded.category,
        brand=excluded.brand,
        model=excluded.model,
        shot_type=excluded.shot_type,
        primary_colors=excluded.primary_colors,
        description=excluded.description,
        tags=excluded.tags,
        width=excluded.width,
        height=excluded.height,
        size_bytes=excluded.size_bytes,
        sha256=excluded.sha256,
        embedding=excluded.embedding;
    """, {
        "original_name": data.get("original_name"),
        "new_name": data.get("new_name"),
        "relative_path": data.get("relative_path"),
        "absolute_path": data.get("absolute_path"),
        "category": data.get("category"),
        "brand": data.get("brand"),
        "model": data.get("model"),
        "shot_type": data.get("shot_type"),
        "primary_colors": colors,
        "description": data.get("description"),
        "tags": tags,
        "width": data.get("width"),
        "height": data.get("height"),
        "size_bytes": data.get("size_bytes"),
        "sha256": data.get("sha256"),
        "embedding": embedding
    })
    
    conn.commit()
    conn.close()

def export_catalog(db_path=DB_PATH, json_path=r"e:\padova moto days 2026\catalog.json", csv_path=r"e:\padova moto days 2026\catalog.csv"):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM photos ORDER BY id ASC")
    rows = cursor.fetchall()
    
    records = []
    for r in rows:
        d = dict(r)
        if d.get("embedding"):
            # Omit huge embedding vector in JSON/CSV export for compactness
            d["has_embedding"] = True
            del d["embedding"]
        else:
            d["has_embedding"] = False
        records.append(d)
        
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
        
    if records:
        fieldnames = list(records[0].keys())
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
            
    conn.close()
    print(f"Catalog esportato in {json_path} e {csv_path}")

if __name__ == "__main__":
    init_db()
    print("Database SQLite inizializzato con successo.")
