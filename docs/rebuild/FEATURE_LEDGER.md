# Registro delle funzioni (FEATURE_LEDGER)

Questo registro elenca tutte le funzioni che Mosaic 2 deve avere e da dove viene ognuna. Applica il metodo della skill REA (`C:\Users\matte\Desktop\rea\.agents\skills\reverse-engineer-anything\SKILL.md`, passo 7: *finding ledger*). Ogni riga collega una funzione all'**evidenza** nel sorgente da cui deriva, alla **decisione** presa per Mosaic 2, alla fase del [PIANO](PIANO.md) e allo stato.

**Come leggerlo**
- **Origine:** `M` = vecchio Mosaic (`_legacy/`); `PP` = PhotoPrism (`C:\Users\matte\Downloads\photoprism-develop\photoprism-develop\`); `N` = nuovo, richiesto dall'utente.
- **Evidenza:** file letto durante l'esplorazione in sola lettura del sorgente, cioè comportamento *osservato nel codice*. Non è un'esecuzione: nessuna delle due app è stata avviata.
- **Stato:** `da fare` / `in corso` / `fatto` / `escluso`. [VERIFICATION.md](VERIFICATION.md) assegna a ogni riga *risolto / parziale / aperto*, con la prova (test o comando). **"fatto" qui vuol dire implementato; solo VERIFICATION dice quanto è provato.**

Percorsi PP relativi alla radice del sorgente; percorsi M relativi a `_legacy/`.

---

## 1. Indicizzazione e libreria

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| IDX-01 | Scansione ricorsiva delle cartelle originali | PP | `internal/photoprism/index.go` (godirwalk) | `os.scandir` ricorsivo su più radici configurabili. Il vecchio Mosaic leggeva solo il primo livello (`algoritmi/01_generate_manifest.py:42-45`) | 1 | fatto |
| IDX-02 | Esclusione di cartelle nascoste, file di ignore e marcatori di storage | PP | `index.go` (`.ppignore`, `.ppstorage`) | Supporto per `.mosaicignore` (sintassi glob) e per un marcatore `.mosaicstorage` | 1 | fatto |
| IDX-03 | Controllo che estensione e tipo MIME reale coincidano | PP | `index.go` | Lettura dei magic bytes; se non coincidono, errore in `errors` | 1 | fatto |
| IDX-04 | Hash del contenuto | PP+M | PP `pkg/fs/hash.go` (SHA-1); M `01_generate_manifest.py:10-15` (SHA-256 a blocchi da 64 KB) | SHA-1 come identità e chiave di cache. Anche SHA-256 calcolato, per abbinare i file al catalogo legacy | 1 | fatto |
| IDX-05 | Indicizzazione incrementale con mod_time | PP | `internal/photoprism/files.go` (`Files.Indexed`) | Cache di `files.mod_time` in memoria: un file con mtime invariato si salta, salvo `--rescan` | 1 | fatto |
| IDX-06 | Sidecar `.xmp` modificato: riaccoda il file principale | PP | `index.go` | Stessa logica | 1 | fatto |
| IDX-07 | Raggruppamento dei file correlati (stack) per prefisso base | PP | `internal/photoprism/mediafile_related.go`, `pkg/fs/stack.go` | Glob `<dir>/<BasePrefix>.*`, togliendo tutte le estensioni note | 1 | fatto |
| IDX-08 | Stacking delle sequenze (` (2)`, ` copy 2`, `.00001`) | PP | `mediafile_related.go` (`StackSequences`) | Opzionale, disattivato per default | 1 | fatto |
| IDX-09 | Edit Apple (`IMG_E1234`) nello stesso stack | PP | `mediafile_related.go` | Incluso | 1 | fatto |
| IDX-10 | Scelta del file principale nello stack | PP | `mediafile_related.go` | Ordine: JPEG/PNG, RAW, HEIC, altre immagini, video | 1 | fatto |
| IDX-11 | Stacking su una foto esistente: path+nome, nome senza sequenza, UUID XMP, meta (lat/lng/data/seriale) | PP | `internal/photoprism/index_mediafile.go` | Stessa sequenza di stacking, protetta da un lock per nome | 1 | fatto |
| IDX-12 | Duplicati esatti: stesso hash, entrambi i percorsi esistono | PP | `index_mediafile.go`, tabella `duplicates` | Tabella `duplicates` e pagina Duplicati | 1 | fatto |
| IDX-13 | Rilevamento di spostamento o rinomina: stesso hash, vecchio percorso sparito | PP | `index_mediafile.go` | Aggiorna il percorso e conserva tutti i metadati. Indispensabile con la rinomina automatica (ORG-01) | 1 | fatto |
| IDX-14 | Anteprima JPEG per formati non visualizzabili (RAW, HEIC) | PP | `internal/photoprism/convert_image_jpeg.go` | Ordine dei convertitori: pillow-heif, rawpy, anteprima incorporata via exiftool. Output in `data/cache/sidecar/` | 1 | fatto |
| IDX-15 | Worker paralleli calibrati sulla RAM | PP | `index_worker.go` (1 worker se RAM < 3 GB) | 2-3 worker su questo PC (8 GB). Coda in SQLite che si riprende dopo un crash | 1 | fatto |
| IDX-16 | Import: copia o sposta nella libreria con nome per data | PP | `internal/photoprism/import.go`, `config/customize/import.go` | Import da cartella o upload. Salta gli hash già presenti, poi indicizza e rinomina | 6 | fatto |
| IDX-17 | Auto-index dopo modifiche alla cartella | PP | `internal/workers/auto/` (ritardo 300 s) | `watchdog` con debounce di 300 s, attivabile da config | 6 | fatto |
| IDX-18 | Pulizia di file mancanti e orfani | PP | `internal/photoprism/cleanup.go`, `purge.go` | Comando `mosaic cleanup`; i file mancanti vengono marcati `missing` | 6 | fatto |
| IDX-19 | Tabella cartelle | PP | entità `folders` | Una riga per cartella, usata dagli smart album per cartella | 1 | fatto |
| IDX-20 | Percorsi non hardcoded | N | M: tutti gli script usano `e:\padova moto days 2026` | Un unico `config.yaml` con procedura guidata al primo avvio | 0 | fatto |

## 2. Metadati

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| META-01 | Lettura EXIF | PP+M | PP `internal/meta/exif.go`; M `01_generate_manifest.py:17-39` (Pillow `_getexif`) | exiftool `-json -n -G` portabile, con fallback su Pillow | 2 | fatto |
| META-02 | JSON di exiftool in cache per hash | PP | `ExifToolCacheName(hash)` | `data/cache/exif/<h0>/<sha1>.json` | 2 | fatto |
| META-03 | Sidecar XMP e XMP incorporato | PP | `internal/meta/xmp.go`, `xmp_document.go` | Titolo, descrizione, keyword, rating e DocumentID. Limiti di sicurezza: 1 MiB e profondità 64 | 2 | fatto |
| META-04 | JSON di Google Takeout | PP | `internal/meta/json_gphotos.go` | Incluso | 2 | fatto |
| META-05 | Ordine delle fonti per la data di scatto | PP | `internal/meta/` più `index_mediafile.go` | Ordine: EXIF, XMP, JSON, data nel nome file, ModifyDate, mtime. Campi `taken_at` (UTC), `taken_at_local`, `time_zone` | 2 | fatto |
| META-06 | Priorità delle fonti per campo (`*_src`) | PP | `internal/entity/src.go` | auto 1 < stima 2 < nome 4 < immagine 8 < onnx 12 < llm 16 < xmp 32 < manuale 64. Una fonte inferiore non sovrascrive mai una superiore | 2 | fatto |
| META-07 | Entità fotocamera e obiettivo normalizzate | PP | `internal/entity/camera*.go`, `lens*.go` | Tabelle `cameras` e `lenses`; ISO, esposizione, f/ e mm salvati sulla foto | 2 | fatto |
| META-08 | Coordinate GPS | PP | `photos.lat/lng/altitude` | Salvate, anche senza mappa (vedi EXT-01) | 2 | fatto |
| META-09 | Stima della posizione da foto vicine nel tempo | PP | `internal/entity/photo_estimate.go` | Rinviata: serve solo con mappa e luoghi | — | escluso |
| META-10 | Backup YAML dei metadati per foto | PP | `internal/entity/photo_yaml.go` | `data/backup/<sha1>.yml`, ripristinato se la foto manca dal DB | 6 | fatto |
| META-11 | Regioni dei volti da XMP (MWG-RS) | PP | `internal/meta/xmp_faces.go` | Importate come marker di fonte `xmp` | 3 | fatto |

## 3. Miniature e colori

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| THB-01 | Catalogo delle taglie | PP+M | PP `internal/thumb/sizes.go`; M `02_generate_thumbnails.py:18` (800 px) | `tile_50/100/224/500` e `fit_720/1280/1920` pre-generate; taglie più grandi su richiesta | 2 | fatto |
| THB-02 | Una taglia derivata dalla più grande già calcolata | PP | `sizes.go` (campo `Source`) | L'originale si decodifica una sola volta | 2 | fatto |
| THB-03 | Layout della cache per hash | PP | `internal/thumb/create.go` | `data/cache/thumbs/<h0>/<h1>/<h2>/<sha1>_<W>x<H>_<fit\|tile>.jpg` | 2 | fatto |
| THB-04 | Qualità JPEG e filtro | PP | `internal/thumb/quality.go` (83), Lanczos | 83, Lanczos, orientamento EXIF applicato, metadati rimossi | 2 | fatto |
| THB-05 | URL delle miniature | PP | `internal/thumb/thumb.go`, `internal/api/thumbnails.go` | `/t/<sha1>/<size>` con cache HTTP immutabile | 7 | fatto |
| THB-06 | Colore principale, griglia 3×3, luminanza, croma | PP | `internal/photoprism/colors.go` | Calcolati sulla taglia `colors` (3×3) | 2 | fatto |
| THB-07 | Nomi italiani dei colori | M | `algoritmi/03_extract_features.py:12-54` | Conservati e usati per filtro `color:` e spiegazioni | 2 | fatto |

## 4. Intelligenza artificiale

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| AI-01 | Embedding immagine | M | `03_extract_features.py:80-90`: softmax delle 1000 classi di MobileNetV2, che non è un vero embedding | **SigLIP 2 base patch16-224** multilingue, ONNX INT8: visione 95 MB, testo 283 MB | 3 | fatto |
| AI-02 | Ricerca testo→foto | N | Assente in M e in PP (PP non ha CLIP) | Embedding del testo SigLIP 2, query in italiano, cosine top-k combinata con i filtri SQL | 3 | fatto |
| AI-03 | Indice vettoriale | M | M: O(n²) brute-force (`05_similarity_engine.py:94-122`) | Matrice numpy float16 in memoria dietro un'interfaccia `VectorIndex` (in seguito hnswlib) | 3 | fatto |
| AI-04 | Modello registrato per ogni vettore | PP | `internal/ai/face` (`embed_model`) | Colonna `model_id`: vettori di modelli diversi non si confrontano mai | 3 | fatto |
| AI-05 | Registro dei modelli | PP | `internal/ai/vision` (`vision.yml`) | `models/models.yaml`, più modalità di esecuzione `on-index` / `newly-indexed` / `on-demand` / `never` | 3 | fatto |
| AI-06 | Etichette e categorie | PP+M | PP `internal/ai/classify` (EfficientFormerV2 + `rules.yml`); M regole sul numero del file (`04_build_catalog.py:19-366`) | **Zero-shot SigLIP 2** su `models/labels_it.yaml`: prompt it/en, categoria, soglia, priorità. Soglia globale 20%, massimo 5 etichette | 3 | fatto |
| AI-07 | Vocabolario del dominio di Mosaic | M | `04_build_catalog.py` (Ducati, Vespa, Ferrari 488 Pista, premiazione, stand, palco…) | Inserito in `labels_it.yaml` | 3 | fatto |
| AI-08 | NSFW a tre valori (safe, unsafe, unavailable) | PP | `internal/ai/nsfw` | Zero-shot calibrato; un risultato unsafe rende la foto privata se `detect_nsfw` è attivo; un risultato incerto non scrive nulla | 3 | fatto |
| AI-09 | Rilevamento volti | PP | `internal/ai/face` (YuNet ONNX, 5 landmark) | YuNet `2023mar` tramite `cv2.FaceDetectorYN` su `fit_720`. Minimo 25 px, retry a 10 px solo se il primo passaggio non trova volti, punteggio ≥ 65, sovrapposizione 42% = stesso volto | 3 | fatto |
| AI-10 | Embedding dei volti | PP | `internal/ai/face` (SFace 128-d, allineamento ArcFace 112 px) | `cv2.FaceRecognizerSF` (SFace `2021dec`); L2-normalizzato | 3 | fatto |
| AI-11 | Clustering dei volti con DBSCAN | PP | `internal/photoprism/faces_cluster.go`, `pkg/vector/alg/clusters.go` | Valori di SFace: ClusterDist 0,72, core 5 poi retry a 4, solo volti ≥ 112 px con punteggio ≥ 85. Ri-clustering ×0,95 per al massimo 6 round. Raggio = 95° percentile, limitato a 0,70 | 3 | fatto |
| AI-12 | Match dei volti con i cluster | PP | `internal/photoprism/faces_match.go`, `entity/face.go` | `dist ≤ min(raggio, 0,70) + 0,25` (massimo 1,25); margine 0,01 tra persone diverse; collisioni e consenso sui nomi | 3 | fatto |
| AI-13 | Ordine dei passi del worker volti | PP | `internal/photoprism/faces.go` (`start`) | Orfani, riferimenti, soggetti, collisioni, ottimizzazione, cluster, match, retry, consenso, pulizia, conteggi | 3 | fatto |
| AI-14 | Persone: nome, unione, separazione, nascondi | PP | entità `subjects`, `faces`, `markers` | Stesse entità; azioni nella pagina Persone | 3/7 | fatto |
| AI-15 | Quasi-duplicati | N | M calcola SHA-256 ma non lo usa mai | pHash (Hamming ≤ 6) oppure cosine SigLIP ≥ 0,97 | 3 | fatto |
| AI-16 | Esecuzione su GPU | N | MX130 da 2 GB; ORT ha solo i provider CPU e Azure | CPU per default; DirectML come opzione da verificare | 3 | fatto |

## 5. Video e Live Photo

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| VID-01 | Metadati video (durata, codec, fps) | PP | `internal/meta/video.go`, `internal/ffmpeg` | `ffprobe -print_format json` (portabile, `runtime/ffmpeg/`) | 4 | fatto |
| VID-02 | Fotogramma poster come anteprima | PP | `internal/ffmpeg` (still frames) | Frame a circa 10% della durata, poi miniature, embedding e volti come per una foto | 4 | fatto |
| VID-03 | Transcodifica H.264 MP4 con faststart | PP | `internal/ffmpeg` (libx264, fallback software, limite 60 Mbps) | Solo se il codec non va nel browser; cache per hash; tetto `fit_1920` | 4 | fatto |
| VID-04 | Streaming con Range | PP | `internal/api/video.go` | `FileResponse` con Range in FastAPI | 7 | fatto |
| VID-05 | Live Photo: video ≤ 3,1 s nello stack di un'immagine | PP | `index_mediafile.go` | Tipo `live`; il frontend lo riproduce al passaggio del mouse | 4 | fatto |
| VID-06 | Motion Photo Google/Samsung (video incorporato) | PP | Rilevato dal JSON di exiftool | Estratto in `data/cache/sidecar/<sha1>.mp4` | 4 | fatto |
| VID-07 | Immagini animate (GIF/WebP multi-frame) | PP | Tipo `animated` | `Pillow.n_frames > 1` | 4 | fatto |
| VID-08 | Accelerazione hardware (NVENC, QSV…) | PP | `internal/ffmpeg` | Esclusa: la MX130 non ha NVENC, si usa libx264 | — | escluso |

## 6. LLM locale

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| LLM-01 | Didascalie ed etichette generate da un LLM | PP | `internal/ai/vision/ollama/const.go`, `vision.yml` (Ollama/OpenAI) | **Incluso nell'installazione**, senza Ollama: `llama-server.exe` (llama.cpp `b11721`, CPU x64) + **Qwen2.5-VL-3B-Instruct Q4_K_M** + mmproj Q8_0 (circa 2,8 GB) | 5 | fatto |
| LLM-02 | Prompt per la didascalia: una frase in voce attiva | PP | `ollama/const.go` | Prompt in italiano: titolo breve, didascalia, parole chiave | 5 | fatto |
| LLM-03 | Output JSON validato con schema | PP | `internal/ai/vision/schema` | Validazione pydantic; un nuovo tentativo se il JSON non è valido | 5 | fatto |
| LLM-04 | Modalità di esecuzione | PP | `vision.yml` (run modes) | `on-demand` (default, D-29) / `newly-indexed` / `never` | 5 | fatto |
| LLM-05 | Ciclo di vita del server LLM | N | — | Avvio su richiesta, solo su 127.0.0.1, spegnimento dopo 5 minuti di inattività; mai in parallelo con SigLIP | 5 | fatto |
| LLM-06 | Fonte `llm` (16) che non sovrascrive XMP né i campi manuali | PP | `entity/src.go` | Vedi META-06 | 5 | fatto |

## 7. Organizzazione

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| ORG-01 | **Rinomina automatica degli originali** | M | `04_build_catalog.py:394-415` (`NNNN_<short_desc>.<ext>`, `os.rename`) | Schema configurabile `{seq:04}_{slug}`. Rinomina l'intero stack; journal prima di ogni operazione; collisioni con suffisso `.00001`; dry-run | 6 | fatto |
| ORG-02 | Rollback delle rinomine | M | `algoritmi/rollback.py` | `mosaic rollback [--batch ID]` dalla tabella `rename_journal` | 6 | fatto |
| ORG-03 | Album manuali | PP | `internal/entity/album.go`, `photos_albums` | Inclusi | 6 | fatto |
| ORG-04 | Smart album con filtro serializzato (cartella, mese, moments) | PP | `album.go` (`album_filter`), `internal/photoprism/moments.go` | Inclusi; soglia dei moments `log2(n)+1`; moments per etichetta e persona | 6 | fatto |
| ORG-05 | Album per stato o paese | PP | `moments.go` | Escluso: richiede i luoghi | — | escluso |
| ORG-06 | Preferiti, privato, archivio (cancellazione logica) | PP | `photos.photo_favorite/private`, `deleted_at` | Inclusi | 6 | fatto |
| ORG-07 | Punteggio qualità e Review | PP | `internal/entity/photo_quality.go` | +3 preferita, +1 per data affidabile, risoluzione e foto fotografica; Review se < 3 | 6 | fatto |
| ORG-08 | Etichette modificabili e categorie | PP | `labels`, `categories`, `photos_labels` | Incluse, con incertezza e fonte | 6 | fatto |
| ORG-09 | Migrazione del catalogo curato del vecchio Mosaic | M | `database/padova_moto_days.db` (334 righe, `sha256`, descrizione, tag, marca, modello) | `mosaic import-legacy`: abbina per SHA-256, fonte manuale (64) | 6 | fatto |
| ORG-10 | Esportazione del catalogo JSON e CSV | M | `algoritmi/db_manager.py:122-150` | `mosaic export --format json\|csv`, senza gli embedding | 6 | fatto |
| ORG-11 | Statistiche nell'intestazione | M | `web_app/index.html:670-674` | Conteggi per tipo, persone, etichette | 7 | fatto |

## 8. Ricerca e similarità

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| SRC-01 | Filtri `key:value` nella query | PP | `internal/form/search.go`, `search_photos.go`, `internal/entity/search/photos.go` | Sottoinsieme indicato nel PIANO, sezione Fase 6 | 6 | fatto |
| SRC-02 | Testo libero | M+PP | M: `index.html:742-751` (sottostringa); PP: keywords | FTS5 su titolo, didascalia, parole chiave, nome e percorso, fuso con il punteggio semantico | 6 | fatto |
| SRC-03 | Ordinamento, paginazione, ordine inverso | PP | `search_photos.go` (`count`, `offset`, `order`, `reverse`) | Inclusi | 6 | fatto |
| SRC-04 | Filtro per categoria | M | `index.html:691-702` (10 pulsanti fissi) | Chip dinamiche generate dalle categorie reali | 7 | fatto |
| SRC-05 | Foto simili con punteggio | M | `05_similarity_engine.py:121` (0,48·cos + bonus fissi), top 6 | `0,6·cos + 0,15·jaccard(etichette) + 0,1·persone + 0,1·tempo + 0,05·colori`, calcolato al momento | 6 | fatto |
| SRC-06 | Spiegazione della similarità sui 4 assi | M | `05_similarity_engine.py:18-72` | Soggetto, inquadratura, palette, contesto, ora derivati da etichette, volti, colori e tempo | 6 | fatto |
| SRC-07 | Pagine Discover (simili, casuale, stagione) | PP | `frontend/src/page/discover` | Simili e casuale | 7 | fatto |
| SRC-08 | Ricerca da CLI | M | `algoritmi/similar.py` (`-q`, `-s`, `-k`) | `mosaic search`, `mosaic similar`, con gli stessi flag | 6 | fatto |

## 9. Interfaccia e API

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| UI-01 | Griglia di foto | M+PP | M `index.html:767-794`; PP `frontend/src/page/photos.vue` | Vue 3, scorrimento infinito, griglia giustificata | 7 | fatto |
| UI-02 | Modale di dettaglio con metadati e simili | M | `index.html:798-884` | Conservato e arricchito con volti, etichette modificabili e fonti dei campi | 7 | fatto |
| UI-03 | Copia del percorso negli appunti | M | `index.html:895-901` | Conservata | 7 | fatto |
| UI-04 | Lightbox | PP | `frontend/src/component/lightbox.vue` (PhotoSwipe 5) | PhotoSwipe 5, con video e Live Photo | 7 | fatto |
| UI-05 | Pagine dell'app | PP | `frontend/src/app/routes.js` | Esplora, Ricerca, Album, Moments, Calendario, Cartelle, Persone, Etichette, Video, Live, Preferiti, Review, Privato, Archivio, Duplicati, Libreria, Impostazioni | 7 | fatto |
| UI-06 | Escape dell'HTML | M | M costruisce l'HTML con stringhe non escapate (`index.html`) | Il template Vue fa l'escape; mai `v-html` su dati utente | 7 | fatto |
| UI-07 | Avanzamento dei job in tempo reale | PP | websocket events | SSE `/api/events` | 7 | fatto |
| UI-08 | Stile visivo e token di colore, tipografia, spazi, raggi, ombre | N | `sift-argus-v3/design-system/tokens/{semantic,foundation,base}.css` | Riusati (UI_SPEC §1); font Outfit + IBM Plex Mono invece di Gilroy | 7 | fatto |
| UI-09 | Sidebar a card: collassabile, drawer su mobile, voce attiva, cerca inline, storico con menu ⋯ | N | `sift-argus-v3/frontend/src/app/shell/Sidebar.tsx`, `Sidebar.module.css`, `AppShell.*` | Riprodotta con le stesse misure e lo stesso comportamento (UI_SPEC §2) | 7 | fatto |
| UI-10 | Transizioni e movimento coerenti, con riduzione del movimento | N | `sift-argus-v3/frontend/src/lib/motion.ts`, `foundation.css` righe 66-83 | Stessi token e costanti delle molle (UI_SPEC §3) | 7 | fatto |
| UI-11 | Icone Lucide con wrapper unico e mappa di nomi | N | `sift-argus-v3/frontend/src/ui/icons.tsx` | Stessa sorgente e stesso wrapper, mappa estesa a foto/video/volti (UI_SPEC §4) | 7 | fatto |
| UI-12 | Tema chiaro/scuro/sistema con cambio animato | N | `sift-argus-v3/frontend/src/app/theme.tsx`, `public/theme-init.js` | Stessa meccanica (View Transitions, `data-theme`) | 7 | fatto |
| UI-13 | Scorciatoie da tastiera (`/`, `?`, `g` + lettera) | N | `sift-argus-v3/frontend/src/lib/hotkeys.ts` | Incluse | 7 | fatto |
| API-01 | API REST | PP | `internal/server/routes.go`, `internal/api/*.go` | FastAPI solo su 127.0.0.1 | 7 | fatto |

## 10. Installazione e avvio

| ID | Funzione | Orig. | Evidenza | Decisione per Mosaic 2 | Fase | Stato |
|---|---|---|---|---|---|---|
| INS-01 | Avvio con doppio clic | M | `Avvia_Mosaic.bat`, `Mosaic.lnk`, `launch_mosaic.pyw:11-39` | `Mosaic.bat` + `.lnk` con `mosaic.ico`: controlla la porta, avvia uvicorn e apre il browser | 8 | fatto |
| INS-02 | Nessun Docker | N | PP si distribuisce con Docker | venv Python + binari portabili in `runtime/` | 0/8 | fatto |
| INS-03 | Asset scaricati e verificati | N | — | `assets.yaml` + `mosaic/assets.py` (ripresa con Range, SHA-256 da LFS/GitHub/checksum ufficiali, `models/assets.lock.json`) | 0 | fatto |
| INS-04 | Script di setup | N | — | `setup.ps1` idempotente: venv, requisiti, `mosaic.assets fetch`, stato | 8 | fatto |
| INS-05 | Procedura guidata al primo avvio | N | — | Scelta della cartella libreria, poi indicizzazione | 7/8 | fatto |

## 11. Esclusi (decisione dell'utente, 18/10/2026)

| ID | Funzione PP | Evidenza | Motivo |
|---|---|---|---|
| EXT-01 | Mappa, luoghi, geocoding inverso | `internal/service/hub/places`, `frontend/src/page/places.vue` | Non selezionato; inoltre PP usa un'API online. Il GPS viene comunque salvato |
| EXT-02 | Multiutente, ruoli, OIDC/LDAP, link di condivisione | `internal/auth`, `entity/link.go` | Non selezionato; l'uso è personale e locale |
| EXT-03 | WebDAV server e client, sincronizzazione | `internal/server/webdav*.go`, `workers/sync*.go` | Non selezionato |
| EXT-04 | Download con yt-dlp, server MCP, modalità cluster | `internal/photoprism/dl`, `internal/mcp` | Fuori ambito |
