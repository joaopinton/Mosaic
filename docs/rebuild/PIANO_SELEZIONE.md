# Piano: «Crea selezione» per album e cartelle

Obiettivo: da un album o da una cartella, scegliere N foto che **riassumono l'evento**: venute bene, il più possibile diverse tra loro, con persone diverse, distribuite lungo tutta la giornata, nel formato scelto.

## Interfaccia

1. **Tasto «Crea selezione»** nell'header della pagina di album, cartella e momento (accanto a cuore e menu).
2. Il tasto **apre la barra laterale** come i Filtri della ricerca (stesso pannello «morph»; sotto i 960 px si apre come drawer). Campi:
   - **Quante foto**: numero con − / + e scelte rapide 12 · 24 · 36 · 50 (1–200).
   - **Formato**: Qualsiasi, Orizzontale, Verticale, Quadrata, 4:3, 3:2, 16:9, 3:4, 2:3, 4:5, 9:16 (stessa tolleranza ±6% della ricerca).
   - **Cosa conta di più**: Qualità · Bilanciata · Varietà.
   - **Più persone diverse** (attivo di default).
   - **Deve esserci** (facoltativo, testo libero come nella ricerca: «palco», «torta»…).
   - **Includi i video** (spento di default).
   - Conteggio dal vivo: «312 foto adatte in questo album».
3. «Crea selezione» porta alla pagina **/selection** (come la ricerca porta a /search): titolo «Selezione · <album>», griglia delle foto scelte in ordine cronologico, e un riquadro «Come ho scelto»: linea del tempo dell'evento con le foto prese, persone presenti, quante scartate perché sfocate/buie o quasi-doppioni.
   - Azioni: **Salva come album**, **Seleziona tutte** (poi la barra della selezione esistente: esporta, preferiti…), **Rigenera** (variante diversa), **Modifica** (riapre la barra con gli stessi valori).

## Algoritmo (`mosaic/organize/curate.py`)

1. **Candidate**: foto dell'album/cartella (`album:<uid>` + formato + testo), escluse archiviate e private; i video solo se richiesti.
2. **Qualità visiva** (0..1), calcolata sulla miniatura `fit_720` e **messa in cache** nel database (nuova tabella `photo_aesthetics`, migrazione 4):
   - nitidezza: varianza del Laplaciano su una griglia 4×4, si prende il 90° percentile delle celle (così lo sfondo sfocato di un ritratto non penalizza);
   - esposizione: pixel bruciati / chiusi, contrasto;
   - estetica SigLIP (se gli embedding ci sono): somiglianza a «foto bella, nitida, ben composta» meno «foto sfocata, mossa, buia, screenshot»;
   - bonus preferite, volti grandi e nitidi; piccola penalità sotto i 2 MP.
   Le foto chiaramente venute male (in fondo alla classifica) vengono scartate quando ce ne sono abbastanza.
3. **Scelta golosa (MMR)** finché si arriva a N:
   `punteggio = qualità + copertura del tempo + persone nuove − somiglianza con le già scelte`
   - quasi-doppioni (raffiche, coseno SigLIP > 0,92) esclusi: si tiene la migliore;
   - tempo: l'evento è diviso in N fasce con lo stesso numero di foto; bonus alle fasce non ancora coperte;
   - persone: bonus a chi non è ancora comparso (persone con nome e gruppi di volti senza nome), così compaiono più persone possibili e in modo equilibrato;
   - pesi diversi per Qualità / Bilanciata / Varietà; «Rigenera» aggiunge una piccola variazione casuale con seme.
4. Risultato in **ordine cronologico**, con i motivi per foto e le statistiche.
5. Senza analisi AI completa funziona lo stesso (tempo + nitidezza + colori), con un avviso.

## API

`POST /api/albums/{ref}/curate` → `{ photos, stats }` (count, ratio/orientamento, text, mode, people, videos, seed). Validazione con Pydantic.

## Test

- Python: qualità (foto nitida > sfocata, buia penalizzata), niente quasi-doppioni, copertura del tempo e delle persone, formato rispettato, conteggio, cache, API 404/400.
- Frontend: pannello (costruzione dei parametri), pagina selezione (salva come album).

## Consegna

Build del frontend, `Mosaic.exe` e `MosaicSetup.exe` ricompilati, decisione D-21 in `DECISIONS.md`, commit.
