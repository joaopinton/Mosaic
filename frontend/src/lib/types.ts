export type PhotoType = 'image' | 'raw' | 'video' | 'live' | 'animated'

export interface PhotoSummary {
  uid: string
  type: PhotoType
  title: string
  caption: string
  taken_at_local: string | null
  width: number
  height: number
  duration: number
  main_color: string
  quality: number
  hash: string | null
  favorite: boolean
  private: boolean
  archived: boolean
}

export interface PhotoList {
  total: number
  semantic: boolean
  photos: PhotoSummary[]
}

export interface PhotoFile {
  name: string
  original_name: string
  path: string
  media_type: string
  size: number
  width: number
  height: number
  codec: string
  primary: boolean
  missing: boolean
}

export interface LabelRef { name: string; slug: string; category: string; confidence: number; source: string }
export interface PersonRef { marker: string; name: string; subject: string | null; face: string | null; x: number; y: number; w: number; h: number }

export interface PhotoDetail {
  uid: string
  type: PhotoType
  title: string
  title_source: string
  caption: string
  caption_source: string
  taken_at: string | null
  time_zone: string
  taken_source: string
  lat: number | null
  lng: number | null
  camera: { make: string; model: string } | null
  lens: string
  iso: number | null
  exposure: string
  f_number: number | null
  focal_length: number | null
  width: number
  height: number
  duration: number
  rating: number
  favorite: boolean
  private: boolean
  archived: boolean
  quality: number
  review: boolean
  main_color: string
  colors: string[]
  hash: string
  seq: number | null
  files: PhotoFile[]
  labels: LabelRef[]
  keywords: string[]
  people: PersonRef[]
  albums: { uid: string; title: string }[]
}

export interface SimilarHit {
  photo: PhotoSummary
  score: number
  cosine: number
  reasons: Record<string, string[]>
}

export interface Album {
  uid: string
  type: 'album' | 'folder' | 'month' | 'moment'
  slug: string
  title: string
  description: string
  smart: boolean
  filter: string
  favorite: boolean
  private: boolean
  photo_count: number
  cover: string | null
  /** album da cui è nata questa selezione salvata (D-27) */
  source?: { uid: string; title: string; type: Album['type'] } | null
  /** cartella sul disco di un album cartella; `root` solo per una cartella data alla libreria, che si può scollegare (D-32) */
  path?: string | null
  root?: string | null
  /** cartella o momento con un nome dato a mano, che la rigenerazione non cambia (D-32) */
  renamed?: boolean
}

export interface PosterFile { name: string; size: number; created: string }

// --- esportazione in una cartella (D-30) ---
export interface EnhanceOptions { light: boolean; contrast: boolean; vivid: boolean; harmonize: boolean; strength: 'light' | 'medium' | 'strong' }
/** Formato di «Ritaglia a formato» (D-34): rapporto w:h o, con `pixels`, misura esatta delle copie. */
export interface CropFormat { w: number; h: number; pixels: boolean }
export interface ExportRequest {
  uids: string[]; dest: string; raw: boolean; videos: boolean; enhance: EnhanceOptions | null; crop: CropFormat | null
  zip: boolean; zip_name: string; fmt: 'original' | 'jpeg' | 'png' | 'webp'; quality: number; max_side: number | null; numbered: boolean
}
export interface ExportPreviewShot { uid: string; before: string; after: string }
export interface CropPreviewShot { uid: string; image: string; box: [number, number, number, number] }

export interface LabelInfo { name: string; slug: string; category: string; count: number; cover: string | null }
export interface Subject { uid: string; name: string; slug: string; photo_count: number; hidden: boolean; favorite: boolean; marker: string | null }
export interface FaceGroup { face: string; samples: number; marker: string }
export interface SuggestedMarker { uid: string; dist: number }
export interface SuggestionGroup { subject: string; name: string; markers: SuggestedMarker[] }
export interface CalendarYear { year: number; months: { month: number; title: string; count: number; album: string; cover: string | null }[] }

export interface Status {
  first_run: boolean
  library_roots: string[]
  photos: number
  videos: number
  analyzed: number
  people: number
  albums: number
  jobs: Record<string, number>
  busy: boolean
  assets: Record<string, boolean>
  ai_ready: boolean
  llm_ready: boolean
  caption_auto: boolean
  rename_auto: boolean
  delete_mode: DeleteMode
}

/** Cosa fa «Elimina» su una foto: toglie solo dal catalogo, o manda anche i file nel Cestino di Windows. */
export type DeleteMode = 'catalog' | 'trash'

export interface RemoveResult { removed: number; files: number; mode: DeleteMode; failed: { uid: string; error: string }[] }
export interface LibraryRoot { path: string; linked: boolean; photos: number }
export interface LibraryRoots { roots: LibraryRoot[]; unlinked: string[] }
export interface ExcludedFile { sha1: string; name: string; path: string; size: number; excluded_at: string; exists: boolean }

export interface Job {
  id: number
  type: string
  state: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  message: string
  error: string
  created_at: string
  started_at?: string | null
  finished_at: string | null
  /** elementi fatti e totali (0 = non noto) */
  done: number
  total: number
}

export interface JobEvent {
  event: 'job'; id: number; type: string; state: Job['state']; progress?: number; message?: string; error?: string
  done?: number; total?: number
}

export interface RenamePreview { photos: number; files: number; skipped: number; ops: { dir: string; from: string; to: string }[] }
export interface RenameBatch { batch_id: string; ts: string; files: number; photos: number; done: number; rolled_back: number; failed: number }

export interface DuplicateExact { photo: PhotoSummary; copies: { path: string; size: number; exists: boolean }[] }
export interface DuplicateNear { photos: PhotoSummary[] }

export interface CloudConfig {
  enabled: boolean
  providers: string[]
  nvidia_model: string
  openrouter_model: string
  parallel: number
  send_names: boolean
}

/** Servizi cloud per le didascalie: da dove viene la chiave (la chiave stessa non arriva mai al browser). */
export interface CloudStatus {
  providers: Record<string, { label: string; key: 'file' | 'env' | null; model: string }>
}

export interface AppConfig {
  library_roots: string[]
  data_dir: string
  import_dir: string
  workers: number
  port: number
  stack_sequences: boolean
  stack_uuid: boolean
  stack_meta: boolean
  auto_index_delay: number
  delete_mode: DeleteMode
  thumbs: { pregenerate: string[]; quality: number }
  ai: { enabled: boolean; model: string; threads: number; provider: string; detect_nsfw: boolean; faces: boolean;
        label_threshold: number; max_labels: number; batch_size: number }
  llm: { mode: 'newly-indexed' | 'on-demand' | 'never'; model: string; idle_seconds: number; threads: number; context: number; parallel: number; language: string; image_size: number; about: string; caption: boolean
         cloud: CloudConfig }
  rename: { auto: boolean; pattern: string; slug_max: number; seq_start: number }
}

// --- «Crea selezione» (D-21) ---
export type CurateMode = 'quality' | 'balanced' | 'variety'
export interface CurateParams { count: number; shape: string; text: string; mode: CurateMode; people: boolean; videos: boolean; seed: number }
export interface CuratedPhoto extends PhotoSummary { reasons: string[] }
export interface CurateStats {
  candidates: number
  selected: number
  rejected: number
  not_chosen: number
  people_total: number
  people_covered: number
  people: { name: string; count: number }[]
  start: string | null
  end: string | null
  /** per ogni fascia dell'evento: [foto candidate, foto scelte] */
  timeline: [number, number][]
  ai_ready: boolean
}
export interface CurateResult { album: Album; photos: CuratedPhoto[]; stats: CurateStats }

// --- trascina e rilascia (D-25) ---
export type DropKind = 'folder' | 'media' | 'zip' | 'unsupported' | 'missing'
export interface DropItem {
  path: string; name: string; kind: DropKind; size: number; media: number; in_library: boolean; contains_library: boolean
  /** Voci che compariranno in «Cartelle» (sottocartelle e zip compresi), zip annidati, nome della cartella di arrivo (D-31). */
  folders: number; zips: number; dest_name: string
}
export interface DropPreview { items: DropItem[]; dest: string | null }

// --- poster a mosaico (D-26) ---
export type PosterPaper = 'a4' | 'a3' | 'a2' | '30x40' | '50x70' | '70x100' | 'square'
export type PosterStyle = 'mondrian' | 'gallery' | 'none'
export type PosterLine = 'thin' | 'medium' | 'bold'
export type PosterFont = 'bahnschrift' | 'georgia' | 'impact' | 'consolas'
export type PosterCase = 'auto' | 'upper' | 'lower'
/** Una scritta di un riquadro colorato (D-33): etichetta piccola facoltativa e valore grande. */
export interface PosterFact { label: string; value: string }
export interface PosterOptions {
  paper: PosterPaper; landscape: boolean; style: PosterStyle; line: PosterLine; colors: number; seed: number; dpi: number
  occasion: string; date: string; place: string
  /** null = dati automatici dell'evento; altrimenti le scritte dell'utente, nell'ordine dei riquadri */
  texts: PosterFact[] | null; font: PosterFont; case: PosterCase
}
/** Testo di un riquadro colorato: misure in frazioni dell'altezza del riquadro, righe già spezzate dal server. */
export interface PosterText { fg: string; label: string; lines: string[]; value: number; label_size: number; pad: number }
export interface PosterCell { x: number; y: number; w: number; h: number; uid: string | null; hash: string | null; color: string | null; crop: [number, number, number, number]; text: PosterText | null }
export interface PosterLayout {
  width_mm: number; height_mm: number; pixels: [number, number]; dpi: number; line: number; ink: string; cells: PosterCell[]
  /** i dati automatici dell'evento, anche quando le scritte sono personalizzate */
  font: PosterFont; facts: PosterFact[]
}
