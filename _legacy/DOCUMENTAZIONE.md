# 🌟 Mosaic - Sistema di Catalogazione, Computer Vision & Motore di Similarità Vettoriale

> **Progetto**: Archivio Fotografico Ufficiale "Padova Moto Days 2026"  
> **Tecnologia**: 100% Locale, Privacy-first, Zero-Cloud, SQLite + ONNX Deep Learning  

---

## 📌 Indice dei Contenuti
1. [Introduzione & Obiettivo](#1-introduzione--obiettivo)
2. [Panoramica dell'Architettura](#2-panoramica-dellarchitettura)
3. [Algoritmo 1: Estrazione Feature & Vettorizzazione Deep Learning](#3-algoritmo-1-estrazione-feature--vettorizzazione-deep-learning)
4. [Algoritmo 2: Matrice di Similarità Ibrida (Cosine Distance + Feature Boosting)](#4-algoritmo-2-matrice-di-similarità-ibrida)
5. [Algoritmo 3: Motore Diagnostico "Perché si Assomigliano" (4 Dimensioni)](#5-algoritmo-3-motore-diagnostico-perché-si-assomigliano)
6. [Struttura del Database SQLite (`padova_moto_days.db`)](#6-struttura-del-database-sqlite)
7. [Guida all'Uso degli Strumenti (Web Dashboard & CLI)](#7-guida-alluso-degli-strumenti)
8. [Sicurezza dei Dati & Procedura di Rollback](#8-sicurezza-dei-dati--procedura-di-rollback)
9. [Come Adattare Mosaic ad Altre Raccolte Fotografiche](#9-come-adattare-mosaic-ad-altre-raccolte-fotografiche)

---

## 1. Introduzione & Obiettivo

**Mosaic** nasce per automatizzare il lavoro più noioso nella gestione di grandi archivi fotografici non strutturati:
* Riconoscere il contenuto reale di ciascuna fotografia ad altissima risoluzione (334 foto scattate da Nikon D850 a 45.7 MP).
* Assegnare a ogni file un nome parlante, pulito e privo di collisioni, mantenendo l'ordinamento cronologico originale.
* Salvare in un database leggero e portabile tutti i percorsi (relativi e assoluti) senza duplicare i pesanti file immagine nel DB.
* Vettorizzare visivamente ogni immagine per permettere la ricerca semantica per similarità e spiegare all'utente **perché** due scatti si assomigliano.

---

## 2. Panoramica dell'Architettura

```
                ┌────────────────────────────────────────────────────────┐
                │             Cartella Foto Originali (E:)               │
                │        Padova Moto Days 0001-.jpg ... 0357-.jpg        │
                └───────────────────────────┬────────────────────────────┘
                                            │
                                  [01_manifest_hash.py]
                                            ▼
                                ┌───────────────────────┐
                                │     manifest.json     │ ◄── Backup Reversibile SHA-256
                                └───────────┬───────────┘
                                            │
                             [02_generate_thumbnails.py]
                                            ▼
                                ┌───────────────────────┐
                                │     thumbnails/       │ ◄── Miniature 800px ottimizzate
                                └───────────┬───────────┘
                                            │
                              [03_extract_features.py]
                   (Inference ONNX MobileNetV2 + Color Quantization)
                                            ▼
                             ┌──────────────────────────────┐
                             │     features_cache.json      │ (Vettori a 1000 dimensioni)
                             └──────────────┬───────────────┘
                                            │
                                 [04_build_catalog.py]
                                            ▼
                   ┌────────────────────────────────────────────────┐
                   │               Rinomina Fisica File             │
                   │  0001_ducati_998s...jpg, 0150_ferrari_488...   │
                   └────────────────────────┬───────────────────────┘
                                            │
                                            ▼
                   ┌────────────────────────────────────────────────┐
                   │         Database SQLite + JSON / CSV           │
                   │             padova_moto_days.db                │
                   └────────────────────────┬───────────────────────┘
                                            │
                              [05_similarity_engine.py]
                                            ▼
                   ┌────────────────────────────────────────────────┐
                   │            similarity_index.json               │
                   │      Top 6 simili + 4 Spiegazioni Visive       │
                   └────────────────────────┬───────────────────────┘
                                            │
                      ┌─────────────────────┴─────────────────────┐
                      ▼                                           ▼
          [Web Dashboard (index.html)]               [CLI Tool (similar.py)]
          UI interattiva con filtri live             Query rapide da terminale
```

---

## 3. Algoritmo 1: Estrazione Feature & Vettorizzazione Deep Learning

Il file responsabile dell'estrazione è **`extract_features.py`**.

### 3.1 Perché MobileNetV2 in formato ONNX?
Per ottenere una vettorizzazione **completamente locale**, senza scaricare gigabyte di modelli cloud o dipendere da schede video dedicate, è stata impiegata una rete **MobileNetV2** serializzata in formato standard ONNX (`mobilenetv2-7.onnx`, 14.2 MB).

### 3.2 Pipeline di Preprocessing
Ogni miniatura viene processata secondo lo standard ImageNet:
1. Ridimensionamento a $224 \times 224$ pixel, 3 canali RGB.
2. Normalizzazione float32 $[0.0, 1.0]$.
3. Standardizzazione z-score:
   $$\text{Input} = \frac{X - \mu}{\sigma}$$
   con $\mu = [0.485, 0.456, 0.406]$ e $\sigma = [0.229, 0.224, 0.225]$.
4. Trasposizione tensoriale in formato NCHW: $(1, 3, 224, 224)$.

### 3.3 Output Vettoriale
La rete genera un tensore di logit a 1000 dimensioni corrispondente alle classi visive. Viene applicata la funzione Softmax e la normalizzazione L2 euclidea:
$$\mathbf{v} = \frac{\text{Softmax}(\mathbf{z})}{\|\text{Softmax}(\mathbf{z})\|_2}$$
Il vettore $\mathbf{v} \in \mathbb{R}^{1000}$ funge da impronta digitale visiva densa per la foto.

---

## 4. Algoritmo 2: Matrice di Similarità Ibrida

Il file responsabile è **`similarity_engine.py`**.

Per garantire che la similarità catturi non solo forme generiche ma anche la semantica specifica del mondo automotive (marca, categoria veicolo, inquadratura), è stata adottata una funzione di affinità ibrida:

$$S_{\text{totale}}(A, B) = w_{\text{cos}} \cdot S_{\text{cos}}(\mathbf{v}_A, \mathbf{v}_B) + \text{Boost}_{\text{semantico}}$$

### Componenti:
1. **Similarità Coseno Visiva**:
   $$S_{\text{cos}}(\mathbf{v}_A, \mathbf{v}_B) = \frac{\mathbf{v}_A \cdot \mathbf{v}_B}{\|\mathbf{v}_A\| \|\mathbf{v}_B\|}$$
2. **Feature Boosting Pesato**:
   * **Stesso Marchio** (es. *Ferrari con Ferrari*, *McLaren con McLaren*): $+0.18$
   * **Stessa Categoria** (es. *Supercar*, *Moto Sportiva*, *Vespa*): $+0.14$
   * **Stessa Tipologia d'Inquadratura** (es. *Macro Ruota/Freno*): $+0.12$
   * **Palette Cromatiche Coincidenti**: $+0.08$

Il punteggio finale viene limitato nell'intervallo $[0.0, 1.0]$ ed espresso come percentuale $0\% - 100\%$.

---

## 5. Algoritmo 3: Motore Diagnostico "Perché si Assomigliano"

A differenza dei tradizionali motori di ricerca vettoriali che restituiscono solo un punteggio freddo, Mosaic confronta le matrici dei metadati e genera **4 spiegazioni puntuali**:

| Dimensione | Logica di Confronto | Esempio Generato |
| :--- | :--- | :--- |
| 🏷️ **Soggetto** | Match di brand, modello e classe | *"Stesso marchio costruttore: Ferrari (modello 488 Pista)"* |
| 📐 **Inquadratura** | Confronto tipologia di campo/prospettiva | *"Entrambe macro ravvicinate focalizzate su cerchio, disco e pinza"* |
| 🎨 **Palette Colori** | Intersezione insiemistica tonalità dominanti | *"Tonalità dominanti condivise: Rosso, nero satinato"* |
| 📍 **Contesto** | Ambiente fisico estratto dalla sequenza temporale | *"Scattate entrambe all'aperto durante il raduno supercar nel cortile"* |

---

## 6. Struttura del Database SQLite (`padova_moto_days.db`)

Il database è strutturato con chiavi e indici per garantire ricerche istantanee in millisecondi:

### Tabella `photos`
```sql
CREATE TABLE photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT UNIQUE NOT NULL,      -- es. 'Padova Moto Days 0150-.jpg'
    new_name TEXT NOT NULL,                 -- es. '0150_ferrari_488_pista_rosso.jpg'
    relative_path TEXT NOT NULL,            -- path relativo nella cartella
    absolute_path TEXT NOT NULL,            -- path completo su disco
    category TEXT,                          -- Supercar, Moto Sportiva, Vespa, ecc.
    brand TEXT,                             -- Ferrari, Ducati, McLaren, ecc.
    model TEXT,                             -- 488 Pista, Panigale V4, ecc.
    shot_type TEXT,                         -- Macro Ruota, Vista 3/4, Palco, ecc.
    primary_colors TEXT,                    -- Rosso, Nero, Grigio, ecc.
    description TEXT,                       -- Sintesi descrittiva in italiano
    tags TEXT,                              -- Tag separati da virgola
    width INTEGER,                          -- Larghezza in pixel
    height INTEGER,                         -- Altezza in pixel
    size_bytes INTEGER,                     -- Peso del file su disco
    sha256 TEXT,                            -- Hash di verifica univoco
    embedding TEXT,                         -- Vettore float JSON a 1000 dim
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Tabella `similarities`
```sql
CREATE TABLE similarities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id_a INTEGER,
    photo_id_b INTEGER,
    score REAL,
    reasons TEXT,                           -- JSON con {soggetto, inquadratura, palette, contesto}
    FOREIGN KEY (photo_id_a) REFERENCES photos(id),
    FOREIGN KEY (photo_id_b) REFERENCES photos(id),
    UNIQUE(photo_id_a, photo_id_b)
);
```

---

## 7. Guida all'Uso degli Strumenti

### 7.1 Avvio dell'Interfaccia Web ("Il Dispositivo")
Basta fare doppio clic sul file **`Avvia_Mosaic.bat`** o eseguire:
```powershell
python launch_mosaic.pyw
```
Il browser si aprirà automaticamente su `http://127.0.0.1:8000/`.

### 7.2 Ricerca da Terminale (CLI)
Il tool `similar.py` permette interrogazioni rapide da console:

```powershell
# Cerca per parola chiave
python similar.py --query "ferrari 488"
python similar.py --query "mclaren"
python similar.py --query "vespa"

# Trova le foto più simili con motivazioni
python similar.py --similar 0250 --top 3
python similar.py --similar 0165 --top 5
```

---

## 8. Sicurezza dei Dati & Procedura di Rollback

Prima di qualsiasi operazione di rinomina, il sistema ha generato il file `manifest.json` contenente l'hash crittografico **SHA-256** di ciascuna foto associato al suo nome di origine.

### Come ripristinare i nomi originali
In qualsiasi momento, per riportare tutti i file ai nomi iniziali (`Padova Moto Days 0001-.jpg`):
```powershell
python rollback.py
```
Il ripristino è immediato e sicuro al 100%.

---

## 9. Come Adattare Mosaic ad Altre Raccolte Fotografiche

Per riutilizzare questa architettura su un altro evento o una cartella diversa di foto:
1. Inserisci le tue immagini in una cartella.
2. Esegui `python generate_thumbnails.py` per generare le miniature veloci.
3. Esegui `python extract_features.py` per estrarre i vettori ONNX.
4. Esegui `python build_catalog.py` per popolare il database SQLite e applicare le regole di rinomina.
5. Esegui `python similarity_engine.py` per creare l'indice delle correlazioni.
6. Apri `index.html` per navigare e cercare.
