# Piano: attività con progresso, trascina e rilascia, sincronizzazione, poster Mondrian

## 1. Caricamento ed elaborazione «da app di spessore»
- **Barra attività** in cima al contenuto (sostituisce la fascia attuale): icona della fase, nome («Analisi AI»), «128 di 450 foto», percentuale, **tempo rimanente stimato**, barra di progresso vera; barra animata indeterminata quando il totale non si conosce ancora (scansione delle cartelle). Clic → pannello Attività.
- **Pannello Attività** (drawer laterale): la **pipeline** come passi (Indicizzazione → Metadati e miniature → Analisi AI → Volti → Album → Didascalie) con stato fatto / in corso / in coda; ogni job con barra, tempo trascorso, tempo rimanente, Annulla; errori leggibili; caricamenti dal trascina e rilascia con il loro progresso.
- **Anello di progresso** nella barra laterale (anche compressa): si vede sempre che Mosaic lavora e a che punto è.
- Backend: `started_at` e conteggi `done/total` strutturati negli eventi dei job (oggi solo testo); la scansione conta i file mentre li trova.

## 2. Trascina e rilascia dentro l'app
- Overlay a tutta finestra mentre trascini («Rilascia per aggiungere a Mosaic»).
- **Nell'app desktop** (Mosaic.exe, WebView2) pywebview dà il **percorso vero** di file e cartelle:
  - **cartella** → di default si **collega dov'è** (diventa una cartella della libreria, niente copie: il percorso originale resta quello); in alternativa «Copia nella libreria»;
  - **foto e video** → copiati nella libreria (importazione per data, mai duplicati);
  - **zip** → estratto in sicurezza (niente percorsi fuori dalla cartella, solo foto/video/XMP, limite di dimensione) in una cartella con il nome dello zip dentro la libreria, poi indicizzato.
  - Dialogo di conferma che elenca cosa è stato rilasciato e cosa succederà.
- **Nel browser** (`python -m mosaic serve`) il percorso non è disponibile: si caricano i file (foto, zip, cartelle intere) con barra di caricamento.

## 3. Sincronizzazione con il computer (Impostazioni)
Due modi, al posto di «Chiarezza / Pulizia»:
- **Solo interfaccia** (predefinito): Mosaic non tocca mai i file. Elimina = la foto sparisce da Mosaic, il file resta.
- **Sincronizzata con il computer**: Elimina = file nel Cestino di Windows; i file cancellati dal computer spariscono anche da Mosaic al controllo successivo; opzione per accorgersi da sola dei cambiamenti nelle cartelle.
- La rinomina automatica (che cambia i nomi sul disco) resta un'impostazione a parte, con un avviso in modalità «Solo interfaccia».

## 4. Poster a mosaico stile Mondrian
- Dalla pagina della selezione: **Crea poster**.
- Formato carta (A4, A3, A2, 30×40, 50×70, 70×100, quadrato), verticale/orizzontale, stile: **Mondrian** (righe nere spesse e qualche riquadro rosso, giallo, blu), **Galleria** (bordi bianchi sottili), spessore delle righe, «Rimescola».
- Impaginazione: suddivisione ricorsiva del foglio (come i quadri di Mondrian) che segue le proporzioni delle foto per tagliarle il meno possibile; le foto migliori prendono i riquadri più grandi; ritaglio centrato sui volti.
- **Anteprima dal vivo** nell'interfaccia; il file finale (JPEG a 200 dpi) si genera sul PC, si salva in `Immagini\Mosaic` e si apre la cartella.

## Consegna
Test (Python e interfaccia), revisione del codice, decisioni D-23…D-26, build dell'interfaccia, `Mosaic.exe` e `MosaicSetup.exe`.
