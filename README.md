# Mosaic 2

Gestore di foto e video **completamente locale**: niente Docker e niente cloud. È una ricostruzione del vecchio Mosaic che adotta le strategie di [PhotoPrism](https://github.com/photoprism/photoprism):
- indicizzazione incrementale con stack RAW+JPEG+XMP+video;
- priorità delle fonti dei metadati;
- volti con clustering;
- smart album e filtri di ricerca;
- video e Live Photo.

In più ci sono un motore di embedding moderno e un LLM locale incluso.

> **Stato:** implementato e verificato con test (284 Python, copertura 90%; 66 frontend) e con una prova end-to-end su una libreria reale. Restano da provare RAW veri, volti su foto con persone e l'installazione su un PC vergine: dettagli in [VERIFICATION.md](docs/rebuild/VERIFICATION.md).

## Cosa fa

- **Ricerca in italiano per contenuto**, come "moto rossa sul palco", con **SigLIP 2** multilingue (ONNX, su CPU), combinata con filtri `label:`, `person:`, `year:`, `camera:`, `color:`, `live:` e altri.
- **Foto simili spiegate**, con un punteggio diviso su soggetto, inquadratura, palette e contesto.
- **Etichette e categorie automatiche** zero-shot, con un vocabolario modificabile (`models/labels_it.yaml`).
- **Volti e persone**, solo per chi è in primo piano (la folla sullo sfondo si ignora): rilevamento YuNet, embedding SFace, clustering DBSCAN, assegnazione dei nomi.
- **Didascalie, titoli e parole chiave in italiano** generati da un LLM locale incluso (Qwen3-VL-2B su llama.cpp). Per andare molto più veloci, in **Impostazioni → Descrizioni con l'AI** si può attivare (facoltativo, spento di default) un servizio cloud gratuito: incolla la chiave di NVIDIA ([build.nvidia.com](https://build.nvidia.com)) e/o di OpenRouter ([openrouter.ai/keys](https://openrouter.ai/keys)). Mosaic prova prima NVIDIA, poi OpenRouter, poi il modello locale; solo le didascalie escono dal PC (vedi D-19 in `docs/rebuild/DECISIONS.md`).
- **Rinomina automatica** degli originali (`0001_ducati-panigale-rossa.jpg`). Rinomina l'intero stack, con journal, dry-run e **rollback**.
- **Video e Live Photo**: anteprime, transcodifica quando serve, streaming.
- **Crea selezione** da un album o da una cartella: scegli quante foto e il formato, Mosaic prende quelle venute meglio, diverse tra loro, lungo tutto l'evento e con più persone possibile, e le salvi come album.
- **Poster a mosaico** stile Mondrian dalla selezione: anteprima dal vivo, formati da A4 a 70×100, file pronto da stampare in `Immagini\Mosaic`.
- **Trascina e rilascia** foto, video, cartelle e file .zip nella finestra: le cartelle si collegano dove sono, senza copie.
- **Attività sempre visibili**: barra di progresso con tempo rimanente, pannello con le fasi dell'elaborazione.
- **Sincronizzazione con il computer** (Impostazioni): Mosaic solo come interfaccia, oppure eliminare in Mosaic = Cestino di Windows e viceversa.
- **Organizzazione**: album, moments, calendario, cartelle, preferiti, privato, archivio, review, duplicati e quasi-duplicati.
- **Migrazione** del catalogo curato del vecchio Mosaic, abbinato per SHA-256.

## Requisiti

- Windows 10/11, Python ≥ 3.12. È testato con 3.14.4.
- Circa 3,5 GB di spazio per modelli e programmi, più la cache delle miniature (circa 0,3 MB per foto).
- Non serve installare nient'altro: exiftool, ffmpeg e llama.cpp vengono scaricati in versione portabile in `runtime/`.

## Installazione su un altro PC

Copia `dist/MosaicSetup.exe` (circa 4 MB) sul PC di destinazione e avvialo. L'installer:

1. chiede in quale disco installare (servono circa 6 GB liberi, 4 GB senza LLM) e crea la sottocartella `Mosaic`;
2. usa Python ≥ 3.12 se presente; altrimenti scarica l'installer ufficiale di Python 3.14.4 da python.org, ne verifica la firma digitale e lo installa dentro la cartella di Mosaic (senza toccare il PATH);
3. crea `.venv` e installa le dipendenze;
4. scarica e verifica con SHA-256 i modelli e i programmi portabili (SigLIP 2, YuNet, SFace, exiftool, ffmpeg, llama.cpp, Qwen2.5-VL-3B);
5. controlla Microsoft Edge WebView2 (già presente su Windows 11) e, se manca, lo installa dal sito Microsoft dopo averne verificato la firma;
6. crea i collegamenti sul Desktop e nel menu Start, registra la disinstallazione in Impostazioni > App e avvia Mosaic.

Serve la connessione a Internet solo durante l'installazione. Windows SmartScreen può avvisare perché l'exe non è firmato: "Ulteriori informazioni" → "Esegui comunque". Il log è in `setup.log` nella cartella di installazione. Se un download si interrompe, rilancia `setup.ps1` dalla cartella installata: riprende da dove era arrivato.

Opzioni di `setup.ps1`: `-InstallDir <cartella>`, `-NoLLM`, `-SkipAssets`, `-Check` (solo controllo), `-Quiet`.

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
```

Per ricostruire l'exe (usa IExpress e il compilatore C# di .NET Framework, entrambi già inclusi in Windows):

```bash
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

Stato degli asset in qualsiasi momento:

```bash
.venv/Scripts/python -m mosaic.assets status
```

## Uso

Mosaic è un'app desktop: il doppio clic su `Mosaic.exe` (o sul collegamento "Mosaic" nel Desktop o nel menu Start) apre una finestra nativa con Edge WebView2. Il server locale (`127.0.0.1:8000`) gira dentro la stessa app e si ferma quando chiudi la finestra. Se WebView2 non è disponibile, Mosaic si apre in una finestra di Edge senza barra degli indirizzi. Al primo avvio una procedura guidata chiede la cartella delle foto. `python -m mosaic serve` avvia ancora la sola versione nel browser.

Per ricompilare solo `Mosaic.exe`:

```bash
powershell -ExecutionPolicy Bypass -File installer/build_launcher.ps1
```

Da riga di comando:

```bash
.venv/Scripts/python -m mosaic index "D:\Foto"
.venv/Scripts/python -m mosaic search "moto rossa sul palco"
.venv/Scripts/python -m mosaic similar <foto>
.venv/Scripts/python -m mosaic rename --dry-run
.venv/Scripts/python -m mosaic rollback --all
.venv/Scripts/python -m mosaic status
```

`mosaic index` esegue tutta la catena: indicizzazione, miniature, analisi, volti, moments, didascalie (se l'LLM è attivo), rinomina e backup. Gli altri comandi (`analyze`, `faces`, `moments`, `caption`, `cleanup`, `import`, `import-legacy`, `export`, `config`, `serve`) fanno un passo alla volta.

## Struttura

| Cartella | Contenuto |
|---|---|
| `mosaic/` | pacchetto Python (backend, CLI, worker) |
| `frontend/` | interfaccia Vue 3 |
| `models/`, `runtime/` | modelli e programmi portabili, scaricati da `assets.yaml` e non versionati |
| `data/` | database e cache, spostabile da `config.yaml` |
| `_legacy/` | il vecchio Mosaic, archiviato e intatto |
| `docs/rebuild/` | documentazione della ricostruzione |

## Documentazione

- [PIANO.md](docs/rebuild/PIANO.md): fasi, file, schema, API, criteri di accettazione.
- [UI_SPEC.md](docs/rebuild/UI_SPEC.md): stile dell'interfaccia (sidebar, transizioni, icone, token) ripreso da sift-argus-v3.
- [FEATURE_LEDGER.md](docs/rebuild/FEATURE_LEDGER.md): tutte le funzioni, con origine (Mosaic o PhotoPrism) ed evidenza nel sorgente.
- [DECISIONS.md](docs/rebuild/DECISIONS.md): le scelte di progetto, separate dal comportamento osservato.
- [VERIFICATION.md](docs/rebuild/VERIFICATION.md): verifica finale funzione per funzione.

## Licenze

Le strategie di PhotoPrism (AGPL-3.0) sono reimplementate senza copiarne il codice. Licenze dei modelli e dei programmi:

| Componente | Licenza |
|---|---|
| SigLIP 2 | Apache-2.0 |
| YuNet | MIT |
| SFace | Apache-2.0 |
| Qwen2.5-VL-3B | Qwen Research (uso personale) |
| llama.cpp | MIT |
| exiftool | Artistic/GPL |
| ffmpeg | GPL |

Dettagli in [DECISIONS.md](docs/rebuild/DECISIONS.md), D-14.
