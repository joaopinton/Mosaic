// Parser lato client delle query `chiave:valore` (specchio di mosaic/search/parser.py), usato per mostrare i filtri come chip.

export const KNOWN_KEYS = [
  'label', 'category', 'keyword', 'person', 'face', 'album', 'folder', 'year', 'month', 'day', 'before', 'after', 'camera',
  'lens', 'iso', 'focal', 'fnumber', 'color', 'type', 'name', 'hash', 'quality', 'title', 'caption', 'uid', 'order', 'count',
  'offset', 'reverse', 'favorite', 'private', 'archived', 'review', 'live', 'video', 'nsfw', 'geo', 'faces', 'portrait',
  'landscape', 'square', 'photo', 'animated', 'raw', 'duplicate', 'missing', 'ratio', 'mp', 'width', 'height', 'mb',
] as const

const ALIASES: Record<string, string> = {
  tag: 'keyword', tags: 'keyword', etichetta: 'label', categoria: 'category', persona: 'person', anno: 'year', mese: 'month',
  giorno: 'day', preferiti: 'favorite', cartella: 'folder', nome: 'name', fotocamera: 'camera', colore: 'color', tipo: 'type',
  prima: 'before', dopo: 'after', privato: 'private', archivio: 'archived', ordine: 'order',
  formato: 'ratio', proporzioni: 'ratio', aspect: 'ratio', megapixel: 'mp', mpx: 'mp', larghezza: 'width', altezza: 'height',
  peso: 'mb',
}

export interface Chip {
  key: string
  value: string
  negate: boolean
  raw: string
}

export interface ParsedQuery {
  chips: Chip[]
  text: string
}

const TOKEN = /([-!]?)(?:([a-zA-Z_]+):)?(?:"([^"]*)"|(\S+))/g

export function parseQuery(query: string): ParsedQuery {
  const chips: Chip[] = []
  const words: string[] = []
  for (const match of query.matchAll(TOKEN)) {
    const [raw, neg, rawKey, quoted, plain] = match
    const key = rawKey ? (ALIASES[rawKey.toLowerCase()] ?? rawKey.toLowerCase()) : ''
    const value = quoted ?? plain ?? ''
    if (key && (KNOWN_KEYS as readonly string[]).includes(key)) {
      chips.push({ key, value, negate: neg !== '', raw })
    } else {
      words.push(quoted !== undefined ? quoted : raw)
    }
  }
  return { chips, text: words.join(' ').trim() }
}

export function chipLabel(chip: Chip): string {
  return `${chip.negate ? '−' : ''}${chip.key}: ${chip.value}`
}

/** Toglie un filtro dalla query lasciando intatto il resto. */
export function removeChip(query: string, chip: Chip): string {
  return query.replace(chip.raw, ' ').replace(/\s{2,}/g, ' ').trim()
}

/** Aggiunge (o sostituisce) un filtro `key:value`, mettendo le virgolette se il valore contiene spazi. */
export function withFilter(query: string, key: string, value: string): string {
  const parsed = parseQuery(query)
  let rest = query
  for (const chip of parsed.chips.filter((c) => c.key === key)) rest = removeChip(rest, chip)
  return `${rest} ${key}:${quote(value)}`.trim()
}

function quote(value: string): string {
  return /\s/.test(value) ? `"${value}"` : value
}

/** Aggiunge un filtro `key:value` senza toccare quelli già presenti (se c'è già identico non lo ripete). */
export function addFilter(query: string, key: string, value: string): string {
  const v = value.trim()
  if (!v || parseQuery(query).chips.some((c) => c.key === key && !c.negate && c.value === v)) return query.trim()
  return `${query.trim()} ${key}:${quote(v)}`.trim()
}

// --- pannello Filtri ------------------------------------------------------------------------------------

export type Orientation = '' | 'landscape' | 'portrait' | 'square'

/** Stato del pannello Filtri. Persone ed etichette vanno in AND, album e colori in OR. */
export interface FilterForm {
  text: string
  type: string
  year: string
  month: string
  camera: string
  orientation: Orientation
  ratio: string
  mp: string
  labels: string[]
  people: string[]
  albums: string[]
  colors: string[]
  favorite: boolean
  review: boolean
  faces: boolean
  geo: boolean
  /** Filtri che il pannello non sa mostrare (negati, iso:…, gruppi in OR…): restano nella query così come sono. */
  extra: string[]
}

export const emptyForm = (): FilterForm => ({
  text: '', type: '', year: '', month: '', camera: '', orientation: '', ratio: '', mp: '',
  labels: [], people: [], albums: [], colors: [], favorite: false, review: false, faces: false, geo: false, extra: [],
})

const SINGLE = ['type', 'year', 'month', 'camera', 'ratio', 'mp'] as const
const FLAGS = ['favorite', 'review', 'faces', 'geo'] as const
const ORIENTATIONS = ['landscape', 'portrait', 'square'] as const
const TRUE = ['true', 'yes', 'si', 'sì', '1', 'on', '']
const splitValues = (value: string): string[] => value.split(/[|,]/).map((v) => v.trim()).filter(Boolean)

/** Precompila il pannello dalla query della ricerca corrente. */
export function formFromQuery(query: string): FilterForm {
  const parsed = parseQuery(query)
  const form = emptyForm()
  form.text = parsed.text
  for (const chip of parsed.chips) {
    if (chip.negate || !takeChip(form, chip)) form.extra.push(chip.raw)
  }
  return form
}

// Copia il filtro nel campo giusto del pannello; false se il pannello non lo sa rappresentare.
function takeChip(form: FilterForm, { key, value }: Chip): boolean {
  const values = splitValues(value)
  const single = (SINGLE as readonly string[]).includes(key) ? (key as (typeof SINGLE)[number]) : null
  if (single) {
    if (form[single] || !value.trim()) return false
    form[single] = value.trim()
    return true
  }
  const flag = (FLAGS as readonly string[]).includes(key) ? (key as (typeof FLAGS)[number]) : null
  if (flag) {
    if (!TRUE.includes(value.toLowerCase())) return false
    form[flag] = true
    return true
  }
  if ((ORIENTATIONS as readonly string[]).includes(key)) {
    if (form.orientation || !TRUE.includes(value.toLowerCase())) return false
    form.orientation = key as Orientation
    return true
  }
  // person:anna|marco è un OR: il pannello mette le persone in AND, quindi lo lascia com'è
  const and = key === 'person' ? form.people : key === 'label' ? form.labels : null
  if (and) {
    if (values.length !== 1) return false
    if (!and.includes(values[0])) and.push(values[0])
    return true
  }
  // un secondo album:… sarebbe in AND col primo: anche questo resta com'è
  const or = key === 'album' ? form.albums : key === 'color' ? form.colors : null
  if (or) {
    if (or.length || !values.length) return false
    or.push(...new Set(values))
    return true
  }
  return false
}

/** Costruisce la query di ricerca dal pannello: `person:anna person:marco album:a|b color:rosso|blu`. */
export function buildQuery(form: FilterForm): string {
  const parts: string[] = []
  if (form.text.trim()) parts.push(form.text.trim())
  for (const key of ['type', 'year', 'month'] as const) if (form[key].trim()) parts.push(`${key}:${quote(form[key].trim())}`)
  for (const v of form.labels) parts.push(`label:${quote(v)}`)
  for (const v of form.people) parts.push(`person:${quote(v)}`)
  if (form.albums.length) parts.push(`album:${quote(form.albums.join('|'))}`)
  if (form.camera.trim()) parts.push(`camera:${quote(form.camera.trim())}`)
  if (form.colors.length) parts.push(`color:${quote(form.colors.join('|'))}`)
  for (const key of ['ratio', 'mp'] as const) if (form[key].trim()) parts.push(`${key}:${quote(form[key].trim())}`)
  if (form.orientation) parts.push(`${form.orientation}:true`)
  for (const key of FLAGS) if (form[key]) parts.push(`${key}:true`)
  parts.push(...form.extra)
  return parts.join(' ')
}
