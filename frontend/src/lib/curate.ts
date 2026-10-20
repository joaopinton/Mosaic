import type { CurateMode, CurateParams } from './types'

// «Crea selezione» (D-21): parametri del pannello ↔ query della pagina /albums/:uid/selection.

export const MAX_COUNT = 200
export const QUICK_COUNTS = [12, 24, 36, 50]
export const DEFAULT_PARAMS: CurateParams = { count: 24, shape: '', text: '', mode: 'balanced', people: true, videos: false, seed: 0 }

export const SHAPES: { value: string; label: string }[] = [
  { value: '', label: 'Qualsiasi' },
  { value: 'landscape', label: 'Orizzontale' },
  { value: 'portrait', label: 'Verticale' },
  { value: 'square', label: 'Quadrata' },
  { value: '4:3', label: '4:3' },
  { value: '3:2', label: '3:2' },
  { value: '16:9', label: '16:9' },
  { value: '3:4', label: '3:4' },
  { value: '2:3', label: '2:3' },
  { value: '4:5', label: '4:5' },
  { value: '9:16', label: '9:16' },
]

export const MODES: { value: CurateMode; label: string }[] = [
  { value: 'quality', label: 'Qualità' },
  { value: 'balanced', label: 'Bilanciata' },
  { value: 'variety', label: 'Varietà' },
]

const SHAPE_RE = /^(landscape|portrait|square|\d{1,2}(\.\d{1,2})?:\d{1,2}(\.\d{1,2})?)$/

export const clampCount = (n: number): number => (Number.isFinite(n) ? Math.min(MAX_COUNT, Math.max(1, Math.round(n))) : DEFAULT_PARAMS.count)

export function shapeLabel(shape: string): string {
  return SHAPES.find((s) => s.value === shape)?.label ?? shape
}

/** Query di ricerca delle candidate, identica a `filter_query` del server: serve a contarle dal vivo. */
export function filterQuery(albumUid: string, p: CurateParams): string {
  const parts = [`album:${albumUid}`]
  if (['landscape', 'portrait', 'square'].includes(p.shape)) parts.push(`${p.shape}:true`)
  else if (p.shape) parts.push(`ratio:${p.shape}`)
  if (!p.videos) parts.push('photo:true')
  if (p.text.trim()) parts.push(p.text.trim())
  return parts.join(' ')
}

/** Parametri → query dell'URL: solo quelli diversi dal predefinito, così l'indirizzo resta corto. */
export function toRouteQuery(p: CurateParams): Record<string, string> {
  const out: Record<string, string> = { n: String(clampCount(p.count)) }
  if (p.shape) out.shape = p.shape
  if (p.text.trim()) out.text = p.text.trim()
  if (p.mode !== DEFAULT_PARAMS.mode) out.mode = p.mode
  if (!p.people) out.people = '0'
  if (p.videos) out.videos = '1'
  if (p.seed) out.seed = String(p.seed)
  return out
}

type RouteQuery = Record<string, unknown>
const str = (v: unknown): string => (typeof v === 'string' ? v : Array.isArray(v) && typeof v[0] === 'string' ? v[0] : '')

/** Query dell'URL → parametri validi: i valori sbagliati tornano al predefinito invece di rompere la pagina. */
export function fromRouteQuery(q: RouteQuery): CurateParams {
  const mode = str(q.mode)
  const shape = str(q.shape)
  const seed = Number.parseInt(str(q.seed), 10)
  return {
    count: str(q.n) ? clampCount(Number(str(q.n))) : DEFAULT_PARAMS.count,
    shape: SHAPE_RE.test(shape) ? shape : '',
    text: str(q.text).slice(0, 300),
    mode: MODES.some((m) => m.value === mode) ? (mode as CurateMode) : DEFAULT_PARAMS.mode,
    people: str(q.people) !== '0',
    videos: str(q.videos) === '1',
    seed: Number.isFinite(seed) && seed > 0 && seed <= 1_000_000 ? seed : 0,
  }
}

export const newSeed = (): number => 1 + Math.floor(Math.random() * 999_999)

const DAY = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'long', year: 'numeric' })
const TIME = new Intl.DateTimeFormat('it-IT', { hour: '2-digit', minute: '2-digit' })

/** Durata dell'evento: «1 maggio 2024, 10:00–18:30» nello stesso giorno, altrimenti «1 maggio 2024 – 3 maggio 2024». */
export function formatSpan(start: string | null, end: string | null): string {
  const a = start ? new Date(start) : null
  const b = end ? new Date(end) : null
  if (!a || Number.isNaN(a.getTime())) return ''
  if (!b || Number.isNaN(b.getTime())) return DAY.format(a)
  if (DAY.format(a) === DAY.format(b)) {
    const from = TIME.format(a)
    const to = TIME.format(b)
    return from === to ? `${DAY.format(a)}, ${from}` : `${DAY.format(a)}, ${from}–${to}`
  }
  return `${DAY.format(a)} – ${DAY.format(b)}`
}
