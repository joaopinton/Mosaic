# Specifica UI di Mosaic 2 (stile di sift-argus-v3)

L'utente ha chiesto di prendere come riferimento la UI di `C:\Users\matte\Desktop\sift-argus-v3`: stile, transizioni, soprattutto la **sidebar**, e la **sorgente delle icone**. Questo documento riassume ciò che è stato **osservato** nel suo codice (lettura in sola lettura, app non avviata) e come lo si applica a Mosaic. I percorsi sono relativi a `sift-argus-v3/`.

> **Framework: Vue 3 (deciso dall'utente il 19/10/2026).** sift-argus-v3 è React 19 + CSS Modules + Radix + `motion`, ma Mosaic resta Vue 3 e riproduce **gli stessi token, le stesse costanti di movimento e la stessa struttura della sidebar**, che non dipendono dal framework. `Sidebar.tsx` serve da riferimento, non si copia. Vedi [DECISIONS.md](DECISIONS.md), D-16.

## 1. Cosa si riusa e da dove

| Elemento | File sorgente in sift-argus-v3 | Uso in Mosaic |
|---|---|---|
| Token semantici (colori chiaro/scuro) | `design-system/tokens/semantic.css` | Riscritti per Mosaic: aggiunti `--stage-*` (dietro le foto), `--overlay-*` (sopra le foto, uguali nei due temi) e `--viewer-bg` (sfondo del visore: nero o grigio 18%, scelta nelle Impostazioni) |
| Fondamenta (spazi, raggi, ombre, durate, curve, altezze, layout) | `design-system/tokens/foundation.css` | Copiati; tolti i token specifici del dominio sift (`--importance-*`, `--origin-*`, `--border-estimated`) |
| Stili base (focus, titoli, `.caps`, numeri tabulari) | `design-system/tokens/base.css` | Copiati |
| Palette (generata) | `frontend/scripts/build-palette.mjs` → `tokens/palettes.css` | **Una sola palette, presa dal logo** (`frontend/public/favicon.svg`): accento rosso `#ff334b`, neutri blu notte dalla piastra, ciano = informazione, giallo = da rivedere, arancio = errore, verde = riuscito; in versione chiara e scura. Più la scala `stage`, grigi senza tinta dietro le foto. Si rigenera con `npm run build:palette` |
| Sidebar | `frontend/src/app/shell/Sidebar.tsx`, `Sidebar.module.css`, `AppShell.*` | Riprodotta, vedi §2 |
| Costanti di movimento | `frontend/src/lib/motion.ts` | Replicate, vedi §3 |
| Icone | `frontend/src/ui/icons.tsx` | Stessa sorgente (Lucide) e stessa mappa, vedi §4 |

**Dipendenza dai browser:** i token usano `light-dark()` e `oklch(from …)`, quindi servono browser recenti (Chrome/Edge/Firefox 2024+). Va bene per l'uso locale su PC aggiornato.

**Regola di sift da rispettare:** i componenti usano **solo token semantici**: niente colori letterali, niente primitivi di palette, niente durate, spazi o raggi letterali. Si porta anche il suo lint (`frontend/scripts/lint-tokens.mjs`) come test di frontend. Il lint controlla anche il **contrasto WCAG** di ogni coppia testo/sfondo dei token, in chiaro e in scuro, e del testo sopra le foto (`lintContrast`).

## 2. Sidebar

**Struttura (espansa).** Una card `.sideCard` con tre zone:
1. **Brand** sopra la card (altezza `--topbar-h` 52 px): logo 32 px + "Mosaic".
2. **Corpo scorrevole:**
   - voce piena **"Importa / Nuova indicizzazione"** (primaria, icona `plus`);
   - **"Cerca"**, che nella stessa riga diventa un campo di ricerca (Esc o blur con campo vuoto lo richiudono; filtra la sezione sotto);
   - **navigazione principale**: Esplora, Album, Persone, Etichette, Video, Preferiti, Review, Archivio, Duplicati;
   - sezione **Recenti / Fissati** con gruppi (`Fissati`, `Oggi`, `Ieri`, `Questa settimana`, `Prima`), ognuno con titolo in maiuscoletto `.caps` (11 px, semibold, tracking 0,06 em).
3. **Piede** con filetto superiore: "Libreria" (stato dei job), "Impostazioni", e il profilo locale.

**Misure e comportamento.**

| Aspetto | Valore |
|---|---|
| Larghezze | `--sidebar-w` 272 px, `--sidebar-w-collapsed` 72 px, `--shell-gap` 12 px |
| Griglia della shell | `grid-template-columns: var(--sidebar-w) minmax(0,1fr)` con `transition: grid-template-columns var(--dur-slow) var(--ease-out)` |
| Card | `--bg-sidebar`, `--radius-shell` 16 px, bordo 1 px `--border-subtle`, `--shadow-shell` |
| Voce | altezza `--nav-item-h` 32 px, `--text-sm`, medio, raggio `--radius-control` 8 px, `padding: 0 --space-2-5` |
| Hover | sfondo `--bg-hover`, testo `--fg-default`; transizione `--dur-fast` `--ease-out` |
| **Attiva** | sfondo `--bg-selected` + testo `--accent-text`, **nessuna barra laterale**; segnalata con `aria-current="page"` |
| Toggle | "bookmark tab" sul bordo destro: 24×44 px, `right:-12px; bottom:24px`, `--shadow-2`, chevron 14 px, `aria-label` "Comprimi/Espandi la barra" |
| Collassata | colonna di bottoni quadrati 36 px (`--control-h`); "Cerca" da collassata espande e apre il campo; tooltip a destra dopo 400 ms |
| Breakpoint | < 960 px: niente sidebar fissa, topbar con hamburger e drawer da sinistra (`min(100vw − 2rem, 20rem)`); 960-1279 px compressa di default; ≥ 1280 px aperta. La scelta dell'utente prevale: `collapsed = preferenza ?? !wide` |
| Persistenza | `localStorage` chiave `mosaic.sidebar` = `collapsed` / `open` |
| Pannello "morph" | un cassetto che sostituisce il contenuto della sidebar (con `AnimatePresence mode="wait"`) e allarga la colonna a `min(520px, 44vw)`. In Mosaic serve per i filtri avanzati e per le impostazioni di un album |
| Riga con menu ⋯ | pulsante 28×28 px, `opacity:0`, visibile su `:hover`, `:focus-within`, `[data-state=open]`; sempre visibile con `(hover: none)`. Voci: Fissa, Rinomina (in linea: Invio salva, Esc annulla), Elimina (con conferma) |
| Pallino di stato | 7×7 px; il pallino "live" pulsa con `pulse-dot 1.6s var(--ease-in-out) infinite` (per i job in corso) |

**Accessibilità.** `nav` con `aria-label`, gruppi in `section aria-label` + `h2`, voci in `ul/li` con link e bottoni nativi, icone `aria-hidden`, bottoni solo-icona con `aria-label`, skip link verso `#main-content`, focus visibile con `--focus-ring`. Il tooltip non sostituisce mai il nome accessibile.

**Scorciatoie.** `/` porta il focus alla ricerca, `?` mostra l'elenco, sequenze `g` poi lettera (timeout 1 s), disattivate mentre si digita.

**Shell.** Tre "tavole" flottanti su `--bg-canvas`: sidebar, card del contenuto (`--bg-surface`, `--radius-shell`, `--shadow-shell`), eventuale barra in basso. Il `body` non scorre; scorre solo la regione di contenuto (`overscroll-behavior: contain; scrollbar-gutter: stable`).

## 3. Transizioni e movimento

**Token CSS** (da `foundation.css`):

```css
--dur-instant: 80ms;  --dur-fast: 140ms;  --dur-base: 220ms;  --dur-slow: 360ms;  --dur-reorder: 420ms;
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
--ease-in:     cubic-bezier(0.55, 0, 1, 0.45);
--ease-sheet:  cubic-bezier(0.32, 0.72, 0, 1);
--ease-spring: linear(0, 0.402 7.4%, 0.711 15.3%, 0.929 23.7%, 1.008 28.2%, 1.067 33%, 1.099 36.9%, 1.12 41.7%, 1.123 45.5%, 1.113 49.9%, 1.081 56.9%, 1.037 66.5%, 1.011 74.3%, 0.999 84.3%, 1);
```

**Costanti per le molle** (da `lib/motion.ts`): `MORPH_SPRING = { stiffness: 520, damping: 38, mass: 0.9 }`; indicatore dei tab `{ 500, 38 }`; barra di ricerca `{ 420, 38 }`; card che entrano `{ 320, 30 }`; stagger 0,03 s con `delayChildren` 0,08 s.

**Regola chiave.** Le durate vivono nei token CSS e il JavaScript le legge dal DOM (`getComputedStyle`). Con `prefers-reduced-motion` i token valgono 0 ms e quindi anche le animazioni JS si azzerano. Inoltre un `MotionConfig reducedMotion="user"` globale.

**Catalogo di effetti da riprodurre**

| Dove | Effetto |
|---|---|
| Cambio pagina | solo dissolvenza: `page-in var(--dur-base) var(--ease-out) backwards`. **Mai scala o traslazioni frazionarie** (fanno sfocare il testo) |
| Larghezza sidebar | transizione di `grid-template-columns` |
| Pannello nella sidebar | `opacity 0→1` + `scale 0.98→1`, 0,2 s; cambio corpo con `x ±10-12 px` |
| Pillola/indicatore attivo (Segmented, Tab, filtri) | shared layout (`layoutId`) con `MORPH_SPRING` |
| Griglia/lista | ingresso `opacity 0, y 8 → 1, 0`; riordino `layout="position"` con `--dur-reorder`; uscita `height: 0`. **Riordino calmo:** l'ordine si congela mentre il puntatore o il focus sono nella lista |
| Dialog | overlay `fade-in`; dialog da `scale(.7)` con `--ease-spring`; uscita `--dur-base` a `scale(.9)`; **cresce dal punto cliccato** (`--origin-dx/--origin-dy` scritti da un listener `pointerdown`) |
| Drawer / sheet | `translateX(∓100%)`, `--dur-slow`, `--ease-sheet` |
| Menu / popover | `pop-in` da `translateY(-4px) scale(.85)` con `--ease-spring`; uscita `pop-out --dur-fast` |
| Toast | `toast-in --dur-slow --ease-spring` da `translateY(--space-3) scale(.92)` |
| Tooltip | 400 ms di ritardo; `tip-in --dur-fast` da `opacity 0, scale(.95), translateY(2px)` |
| Skeleton | `shimmer 1.6s var(--ease-in-out) infinite`; stesse dimensioni del contenuto finale |
| Bottone | `:active` → `translateY(1px)`; in caricamento mantiene la larghezza (spinner assoluto) |
| Cambio tema | View Transitions API, dissolvenza unica di 450 ms (`cubic-bezier(0.45,0,0.25,1)`); durante il passaggio le transizioni CSS sono spente (`data-theme-fade`) |
| Hover delle card | nessun `transform`: cambiano solo bordo e ombra |

**Regole di stile da rispettare:** nessun ritardo artificiale; i caricamenti non usano spinner a tutto schermo; nessun lampeggio sopra 3 Hz.

## 4. Icone

- **Sorgente:** Lucide (sift usa `lucide-react`; per Vue: `@lucide/vue`, perché `lucide-vue-next` è deprecato).
- **Wrapper unico `Icon`:** `size = 1rem (16 px)`, `strokeWidth = 1.6`, sempre `aria-hidden="true"` e `focusable="false"`; fallback `LayoutGrid` se il nome manca.
- **Dimensioni usate:** 12 (chiudi nel campo), 14 (chevron del toggle), 16 (standard e ⋯), 18 (intestazioni di pannello).
- **Mappa di nomi semantici** (`ui/icons.tsx`), estesa per Mosaic:

| Nome | Lucide | Uso |
|---|---|---|
| plus, check, x, search, settings, more, menu, copy, trash, edit, pin, star, user, users, info, alert, list, grid, eye | Plus, Check, X, Search, Settings, MoreHorizontal, Menu, Copy, Trash2, Pencil, Pin, Star, User, Users, Info, AlertTriangle, List, LayoutGrid, Eye | azioni comuni |
| chevronDown/Up/Left/Right, sidebar, sliders, sun, moon, monitor, keyboard, download, external, undo, redo, spinner, sparkle, logout | Chevron*, PanelLeft, SlidersHorizontal, Sun, Moon, Monitor, Keyboard, Download, ExternalLink, Undo2, Redo2, Loader2, Sparkles, LogOut | shell e impostazioni |
| **Nuove per Mosaic:** image, images, video, play (live), camera, tag, folder, album (`BookOpen`), people (`ScanFace`), heart (`Heart`), archive, lock (privato), copyCheck (duplicati, `CopyCheck`), review (`ClipboardCheck`), calendar, wand (`Wand2`, LLM), cpu (job), hardDrive (libreria) | Image, Images, Video, Play, Camera, Tag, Folder, BookOpen, ScanFace, Heart, Archive, Lock, CopyCheck, ClipboardCheck, Calendar, Wand2, Cpu, HardDrive | specifiche di Mosaic |

## 5. Tipografia: differenza fra documentazione e codice

La documentazione di sift parla di IBM Plex Sans + Source Serif 4, ma **il codice reale usa Gilroy per tutta l'interfaccia** (`foundation.css` righe 7-14) e IBM Plex Mono per numeri, codici e conteggi.

- **Gilroy è un font con licenza commerciale**: non lo si copia in Mosaic.
- **Per Mosaic:** `--font-display` e `--font-ui` = **Outfit** (già usato dal vecchio Mosaic, licenza OFL, scaricabile in locale con `@fontsource/outfit`), `--font-mono` = **IBM Plex Mono** (OFL, `@fontsource/ibm-plex-mono`). I font vanno serviti in locale, mai da CDN, per rispettare "tutto in locale".
- Scala: base 15 px (`--text-2xs` 11 · `xs` 12 · `sm` 13 · `base` 15 · `md` 17 · `lg` 20 · `xl` 24); interlinee 1,2 / 1,35 / 1,55.

## 6. Correzioni da applicare ai token copiati

Nel sorgente di sift mancano due token usati dai componenti. Nel port vanno definiti:
- `--weight-bold: 700`;
- `--danger-text` → alias di `--deviates-fg`.

Il peso 800 del logotipo diventa un token (`--weight-brand`).

## 7. Mappatura dei colori di stato

Sift ha quattro colori di dominio. In Mosaic si rinominano senza cambiare i valori:

| Token sift | Significato in Mosaic |
|---|---|
| `--match-*` (verde) | riuscito, indicizzato, foto riconosciuta |
| `--to-verify-*` (ambra) | da rivedere (Review), qualità bassa, stima |
| `--deviates-*` (rosso) | errore, elimina, NSFW |
| `--alternative-*` (blu) | informazione, in corso |

**Regola di sift:** l'accento indica azione e selezione; i colori di stato indicano esito. Non si mescolano. Ogni stato si riconosce anche per **forma e parola**, non solo per colore.

## 8. Test e verifica della UI

- vitest sul parser di query lato client e sui componenti con `axe-core` (ruoli e nomi accessibili, come `Sidebar.test.tsx` di sift).
- Controllo dei token con uno script equivalente a `lint-tokens.mjs`.
- Screenshot a 320, 768, 1024, 1440 px in chiaro e scuro **solo su richiesta esplicita dell'utente**.
