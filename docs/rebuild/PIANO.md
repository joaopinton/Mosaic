# Piano di implementazione di Mosaic 2

Il piano è stato approvato dall'utente il 18/10/2026. Questo documento lo trasforma in attività, file da creare e criteri di accettazione per ogni fase.
- **Cosa deve fare il sistema:** [FEATURE_LEDGER.md](FEATURE_LEDGER.md), con gli ID citati qui sotto.
- **Perché è fatto così:** [DECISIONS.md](DECISIONS.md).
- **Come si chiude il lavoro:** [VERIFICATION.md](VERIFICATION.md).

## Stato al 19/10/2026

| Voce | Stato |
|---|---|
| Vecchio Mosaic archiviato in `_legacy/`, nessun file cancellato | ✅ |
| `.venv` (Python 3.14.4) con tutte le dipendenze, `git init` | ✅ |
| `assets.yaml` + `mosaic/assets.py` + `mosaic/paths.py` (download verificati) | ✅ |
| Asset core e LLM: SigLIP 2, YuNet, SFace, exiftool, ffmpeg, llama.cpp, Qwen2.5-VL-3B | ✅ scaricati e verificati (`python -m mosaic.assets status`) |
| Documentazione: LEDGER, DECISIONS, PIANO, VERIFICATION, UI_SPEC, README | ✅ |
| Codice delle fasi 1-8 (backend, CLI, API, frontend Vue, installer) | ✅ implementato |
| Fase 9, verifica: 284 test Python (copertura 90%), 66 test frontend, 25 controlli su server reale, prova end-to-end su 22 foto con rollback | ✅ vedi [VERIFICATION.md](VERIFICATION.md) |
| `dist/MosaicSetup.exe`: installer per un altro PC, costruito da `installer/build_installer.ps1` | ✅ testato su installazione pulita in una cartella di prova; Python da python.org e download dei modelli **non provati** su un PC vergine |
| Aperti (vedi VERIFICATION): RAW reale, volti su foto con persone, calibrazione etichette, aspetto dell'interfaccia a schermo, abbinamento delle 334 foto legacy | ⚠ |

**Dipendenze installate in `.venv` e verificate:**

| Pacchetto | Versione |
|---|---|
| numpy | 2.5.4 |
| pillow | 13.0 |
| onnxruntime | 1.31 |
| opencv-python-headless | 5.0 (con `FaceDetectorYN` e `FaceRecognizerSF`) |
| fastapi | 0.146 |
| uvicorn | 0.54 |
| pydantic | 2.14 |
| tokenizers | 0.23 |
| pillow-heif | 1.8 |
| rawpy | 0.27 |
| watchdog | 6.0 |
| imagehash | 4.3 |
| pyyaml | — |
| httpx | — |
| huggingface_hub | — |
| pytest + pytest-cov | — |

SQLite di sistema: 3.50.4, con FTS5.

---

## Struttura del repository

```
Mosaic/
  _legacy/            vecchio Mosaic (sola lettura, fonte per la migrazione)
  assets.yaml         manifesto degli asset scaricabili
  config.yaml         configurazione utente (creata al primo avvio)
  data/               mosaic.db, cache/{thumbs,exif,sidecar,video}, backup/  (spostabile)
  docs/rebuild/       documentazione del rebuild
  frontend/           Vue 3 + Vite; dist/ compilato
  models/             ONNX, GGUF, labels_it.yaml, models.yaml, assets.lock.json
  mosaic/             pacchetto Python (moduli elencati per fase)
  runtime/            exiftool/, ffmpeg/, llama/
  tests/              pytest (unit, integrazione, e2e) + fixtures/
  setup.ps1, Mosaic.bat, Mosaic.lnk, mosaic.ico, pyproject.toml, requirements.txt
```

Regole di codice, dalle regole utente `~/.claude/rules/ecc`:
- file sotto le 400 righe;
- funzioni sotto le 50 righe;
- type hint ovunque;
- dataclass `frozen` per i DTO;
- `logging` invece di `print` (eccetto CLI e `assets.py`);
- errori gestiti in modo esplicito e registrati nella tabella `errors`;
- input validati ai confini (API con pydantic, file con i magic bytes).

---

## Fase 1: Core (config, DB, indicizzazione)
**Funzioni:** IDX-01…15, IDX-19, IDX-20.

**File**
- `mosaic/config.py`: dataclass `Config`, cioè `library_roots[]`, `data_dir`, `workers`, `rename`, `ai`, `llm`, `thresholds`. Carica e valida `config.yaml`, con valori di default.
- `mosaic/db/schema.sql`, `mosaic/db/migrate.py`, `mosaic/db/connection.py`: WAL, `foreign_keys=ON`, una connessione per thread.
- `mosaic/index/discover.py`: scansione, ignore, magic bytes.
- `mosaic/index/related.py`: `base_prefix()`, `related_files()`, scelta del file principale.
- `mosaic/index/hashing.py`: SHA-1 e SHA-256 in una sola lettura.
- `mosaic/index/indexer.py`: pipeline per gruppo, stacking, duplicati e spostamenti.
- `mosaic/jobs/queue.py`, `mosaic/jobs/worker.py`: tabella `jobs` (tipo, payload, stato, tentativi, errore), pool di thread.

**Schema essenziale**

| Tabella | Colonne / contenuto |
|---|---|
| `photos` | `id`, `uid`, `uuid`, `type`, `title`, `title_src`, `caption`, `caption_src`, `taken_at`, `taken_at_local`, `taken_src`, `time_zone`, `lat`, `lng`, `altitude`, `camera_id`, `lens_id`, `camera_serial`, `iso`, `exposure`, `f_number`, `focal_length`, `width`, `height`, `duration`, `favorite`, `private`, `quality`, `stack_key`, `nsfw`, `deleted_at`, `created_at`, `updated_at` |
| `files` | `id`, `photo_id`, `root`, `path`, `name`, `original_name`, `sha1`, `sha256`, `size`, `mod_time`, `mime`, `media_type`, `primary`, `sidecar`, `missing`, `codec`, `width`, `height`, `orientation`, `duration`, `fps`, `colors`, `main_color`, `luminance`, `chroma`, `phash`, `error` |
| `folders` | cartelle della libreria |
| `duplicates` | file con lo stesso hash in percorsi diversi |
| `errors` | errori registrati durante le operazioni |
| `jobs` | coda dei job |
| `cameras`, `lenses` | fotocamere e obiettivi normalizzati |
| `labels`, `categories`, `photos_labels` | `uncertainty`, `src` |
| `keywords`, `photos_keywords` | parole chiave |
| `embeddings` | `photo_id`, `model_id`, `vector` BLOB f16 |
| `markers`, `faces`, `subjects` | volti |
| `albums`, `photos_albums` | `album_filter` per gli smart album |
| `rename_journal` | `batch_id`, `file_id`, `old_path`, `new_path`, `state`, `ts` |
| `photos_fts` | FTS5 su titolo, didascalia, parole chiave, nome, percorso |

**Accettazione**
- Uno stack di prova `IMG_1.jpg` + `IMG_1.CR2` + `IMG_1.jpg.xmp` + `IMG_1.mov` produce 1 foto e 4 file, con primario il JPEG.
- Un secondo index senza modifiche salta il 100% dei file.
- Una copia dello stesso file in un'altra cartella finisce in `duplicates`.
- Un file spostato conserva `photo_id` e i metadati.

## Fase 2: Metadati, miniature, colori
**Funzioni:** META-01…08, META-10, META-11, THB-01…07.

**File**
- `mosaic/meta/exiftool.py`: processo `-stay_open` riusato, cache JSON per hash.
- `mosaic/meta/extract.py`: normalizza in una dataclass `MetaData`.
- `mosaic/meta/xmp.py`, `mosaic/meta/takeout.py`.
- `mosaic/meta/sources.py`: costanti delle fonti e `assign(field, value, src)`, che rispetta la priorità.
- `mosaic/meta/dates.py`: ordine di estrazione della data e parsing dei nomi file.
- `mosaic/meta/camera.py`: normalizzazione di marca e modello.
- `mosaic/thumbs/sizes.py`, `mosaic/thumbs/render.py`: Pillow Lanczos, catena di sorgenti, layout della cache.
- `mosaic/thumbs/convert.py`: HEIC/RAW verso un JPEG sidecar.
- `mosaic/thumbs/colors.py`: griglia 3×3, nomi italiani da `_legacy/algoritmi/03_extract_features.py:12-54`.

**Accettazione**
- Data, fotocamera, ISO e GPS sono estratti correttamente da una fixture JPEG con EXIF noti.
- Un titolo XMP non viene sovrascritto da un titolo `llm`.
- Le miniature finiscono nel percorso atteso e l'originale si decodifica una volta sola (verificato con mock).

## Fase 3: Motore AI
**Funzioni:** AI-01…16, META-11.

**File**
- `mosaic/ai/runtime.py`: sessioni ONNX lazy, scelta del provider, scaricamento.
- `mosaic/ai/siglip.py`: `embed_images(batch)`, `embed_texts(list)`. Preprocessing letto da `preprocessor_config.json`.
- `mosaic/ai/vectors.py`: `VectorIndex` (`add`, `remove`, `search(vec, k, mask)`), caricamento dal DB.
- `mosaic/ai/labels.py` + `models/labels_it.yaml`: vocabolario di 300-500 etichette, in partenza dal dominio di Mosaic. Gli embedding dei prompt sono calcolati una volta e messi in cache.
- `mosaic/ai/nsfw.py`: prompt calibrati, risultato a tre valori.
- `mosaic/ai/faces.py`: rilevamento con YuNet, retry, allineamento ed embedding con SFace.
- `mosaic/ai/cluster.py`: DBSCAN euclideo in numpy, ri-clustering, centroide, raggio al 95° percentile.
- `mosaic/ai/face_worker.py`: gli 11 passi di AI-13.
- `mosaic/ai/neardup.py`: pHash con `imagehash` più cosine.
- `models/models.yaml`: registro con id, file, dimensioni, preprocessing, modalità di esecuzione.

**Accettazione**
- Gli embedding SigLIP coincidono con il riferimento: cosine ≥ 0,99 su 5 immagini.
- "moto rossa" mette una foto di una moto rossa in top-3 su una fixture di 20 immagini.
- DBSCAN su vettori sintetici (3 gruppi più rumore) ritrova 3 cluster.
- Il match rispetta il margine di 0,01 e il tetto di 1,25.

## Fase 4: Video e Live Photo
**Funzioni:** VID-01…07.

**File**
- `mosaic/video/probe.py`
- `mosaic/video/poster.py`
- `mosaic/video/transcode.py`: libx264, `-movflags +faststart`, tetto 1920 px, cache per hash.
- `mosaic/video/live.py`: Live Photo ≤ 3,1 s, Motion Photo, animate.

**Accettazione**
- Un MP4 sintetico generato con ffmpeg `testsrc` produce poster, durata e codec.
- Un JPEG più un MOV da 2 s nello stesso stack diventa tipo `live`.
- Un HEVC viene transcodificato una sola volta.

## Fase 5: LLM locale
**Funzioni:** LLM-01…06.

**File**
- `mosaic/llm/server.py`: avvio di `runtime/llama/llama-server.exe -m <gguf> --mmproj <mmproj> --host 127.0.0.1 --port <libera> -c 4096 -t 6`, controllo di salute, spegnimento dopo inattività.
- `mosaic/llm/captions.py`: prompt italiano, immagine `fit_720` in base64, schema pydantic `{title, caption, keywords[]}`, un nuovo tentativo.
- `mosaic/llm/worker.py`: modalità `newly-indexed`, `on-demand`, `never`.

**Accettazione**
- Test con un server finto (httpx mock) per schema, nuovo tentativo e priorità delle fonti.
- Smoke reale su 3 foto, con il tempo per foto registrato.

## Fase 6: Organizzazione, rinomina, ricerca
**Funzioni:** IDX-16…18, ORG-01…11, SRC-01…08.

**File**
- `mosaic/organize/rename.py`: schema, slug (ASCII, minuscolo, massimo 60 caratteri), stack intero, journal, collisioni, dry-run.
- `mosaic/organize/rollback.py`
- `mosaic/organize/albums.py`, `mosaic/organize/moments.py`, `mosaic/organize/quality.py`
- `mosaic/organize/legacy.py`: import del catalogo legacy per SHA-256.
- `mosaic/organize/export.py`, `mosaic/organize/importer.py`, `mosaic/organize/watcher.py`, `mosaic/organize/cleanup.py`
- `mosaic/search/parser.py`: `key:value`, virgolette, negazione `-`, alias.
- `mosaic/search/query.py`: SQL più FTS5 più semantico, con fusione dei punteggi.
- `mosaic/search/similar.py`: formula D-10 e spiegazioni sui 4 assi.
- `mosaic/cli.py`: comandi `index`, `import`, `search`, `similar`, `faces`, `caption`, `rename`, `rollback`, `export`, `import-legacy`, `cleanup`, `serve`.

**Filtri di ricerca supportati**
- Etichette e persone: `label`, `category`, `person`, `face`.
- Collezioni: `album`, `folder`.
- Tempo: `year`, `month`, `before`, `after`.
- Fotocamera e colore: `camera`, `lens`, `iso`, `color`.
- Tipo: `type`, `live`, `video`.
- Stato: `favorite`, `private`, `archived`, `review`.
- Orientamento: `portrait`, `landscape`, `square`.
- Altro: `name`, `hash`, `quality`.
- Paginazione e ordinamento: `count`, `offset`, `order`, `reverse`.

**Accettazione**
- Rinomina più rollback su uno stack di 4 file con una collisione: i nomi tornano identici byte per byte e gli hash non cambiano.
- Dopo una rinomina, il reindex trova lo spostamento e non crea nuove foto.
- Il parser ha test per ogni chiave.
- La migrazione legacy importa i campi solo per gli SHA-256 che corrispondono.

## Fase 7: API e frontend
**Funzioni:** THB-05, VID-04, UI-01…07, API-01, SRC-04, SRC-07, ORG-11.

**API** (`mosaic/api/`, FastAPI, solo su `127.0.0.1`)

| Gruppo | Endpoint |
|---|---|
| Foto | `GET /api/photos?q=` (ricerca), `GET /api/photos/{uid}`, `PATCH /api/photos/{uid}`, `POST /api/photos/{uid}/favorite\|private\|archive\|restore` |
| Ricerca e simili | `GET /api/photos/{uid}/similar`, `GET /api/search/suggest` |
| File | `GET /t/{sha1}/{size}` (miniature), `GET /api/files/{uid}/original`, `GET /api/videos/{uid}` (Range) |
| Album, etichette, persone | `GET/POST /api/albums`, `GET /api/labels`, `GET /api/subjects`, `POST /api/markers/{id}` (assegna nome), `POST /api/subjects/merge` |
| Duplicati | `GET /api/duplicates` |
| Libreria | `POST /api/library/index\|import\|cleanup`, `POST /api/rename/preview\|apply\|rollback` |
| Job e impostazioni | `GET /api/jobs`, `GET /api/events` (SSE), `GET/PUT /api/settings`, `GET /api/status` (asset, modelli, LLM) |

**Frontend** (`frontend/`, Vue 3, Vite, vue-router, PhotoSwipe 5)
- Pagine come in UI-05.
- Componenti: `PhotoGrid` (scorrimento infinito, griglia giustificata), `PhotoDetail` (modale come il vecchio Mosaic più volti, etichette e fonti), `SearchBar` (suggerimenti `key:`), `JobsPanel` (SSE), `SetupWizard`.
- Grafica, sidebar, transizioni e icone: **stile di sift-argus-v3**, specifica completa in [UI_SPEC.md](UI_SPEC.md).
  - Token copiati da `sift-argus-v3/design-system/tokens/` in `frontend/src/styles/`; tema chiaro/scuro con `data-theme` e `data-palette` sul `<html>`, applicato prima del primo render.
  - Sidebar a card con toggle "bookmark tab" (272/72 px), drawer sotto 960 px, stato salvato in `localStorage`.
  - Movimento con le durate e le curve dei token e le molle di `lib/motion.ts`; tutto azzerato con `prefers-reduced-motion`.
  - Icone Lucide con un unico wrapper (16 px, stroke 1,6).
  - Font **Outfit** + **IBM Plex Mono**, serviti in locale (Gilroy di sift è un font commerciale).
  - Libreria di animazione: `motion-v` (Vue); da verificare in fase 7. Se l'utente passa a React, si riusa direttamente `Sidebar.tsx` (D-16).
- `npm run build` produce `frontend/dist/`, servito da FastAPI su `/`.

**Accettazione**
- `TestClient`: ricerca, dettaglio, miniature 200 con cache, video con Range 206, job via SSE.
- vitest: parser della query lato client.

## Fase 8: Installazione e avvio
**Funzioni:** INS-01…05.

**File**
- `setup.ps1`: controlla Python ≥ 3.12, crea `.venv`, `pip install -r requirements.txt`, `python -m mosaic.assets fetch --group core` (con `-WithLLM`, anche `--group llm`), stampa lo stato. È idempotente.
- `mosaic/app.py`: controllo della porta (logica di `_legacy/launch_mosaic.pyw:11-18`), uvicorn, worker, apertura del browser.
- `Mosaic.bat`: `start "" "%~dp0.venv\Scripts\pythonw.exe" -m mosaic.app`.
- `Mosaic.lnk`: rigenerato con PowerShell (WScript.Shell) che punta a `Mosaic.bat`, cartella di lavoro relativa, icona `mosaic.ico`.

**Accettazione**
- `setup.ps1` su un clone pulito arriva a "Tutti gli asset sono pronti".
- Il doppio clic su `Mosaic.bat` apre la UI.
- Nessun riferimento a `E:\` nel codice: `grep -ri "e:\\\\" mosaic` non trova nulla.

## Fase 9: Test e verifica finale
- `pytest --cov=mosaic --cov-report=term-missing`, con almeno l'80% su `mosaic/` (esclusi `api/` e `llm/server.py`, coperti dai test di integrazione).
- Fixture generate dai test stessi: immagini Pillow con EXIF e XMP scritti con exiftool, video `testsrc` di ffmpeg. Nessun dato personale nel repository.
- Smoke end-to-end su circa 30 file misti: i comandi sono in [VERIFICATION.md](VERIFICATION.md).
- La preview nel browser si fa **solo su richiesta esplicita** dell'utente (regola performance).
- Chiusura col metodo REA: ogni riga del LEDGER passa a *risolto / parziale / aperto* in VERIFICATION.md, con la prova.

---

## Ordine e dipendenze

```
F1 core ──► F2 meta/thumbs ──► F3 AI ──► F6 ricerca/organizzazione ──► F7 API+UI ──► F8 setup ──► F9 verifica
                     └──────► F4 video ──┘          ▲
                                F5 LLM ──────────────┘ (serve F2 per fit_720, F6 per lo slug di rinomina)
```

**Parallelizzabili una volta chiusa F2:** F3, F4 e F5 toccano moduli diversi (`ai/`, `video/`, `llm/`). Il frontend (F7) si può iniziare in parallelo a F6, partendo dal contratto API scritto sopra.

## Rischi aperti
1. **Spazio su C:.** Circa 7 GB liberi prima dei download; dopo tutti gli asset ne restano circa 3,5. Conviene spostare `data/` su un altro disco se la libreria è grande.
2. **Preprocessing di SigLIP 2 nell'export della community.** Va verificato in F3 contro un riferimento.
3. **YuNet `2023mar` invece di `2026may`.** Le soglie potrebbero richiedere una ricalibrazione.
4. **Tempo dell'LLM su CPU (20-60 s per foto).** Gira solo in background, interrompibile.
5. **Foto originali dell'evento assenti dal PC.** Erano sul disco E:. Senza la cartella reale la migrazione legacy resta da verificare.
