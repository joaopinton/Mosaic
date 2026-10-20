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
  const quoted = /\s/.test(value) ? `"${value}"` : value
  return `${rest} ${key}:${quoted}`.trim()
}
