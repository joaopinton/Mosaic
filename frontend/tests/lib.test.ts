import { describe, expect, it, vi } from 'vitest'
import { formatBytes, formatDate, formatDuration, groupHistory } from '../src/lib/format'
import { aspect, justify } from '../src/lib/layout'
import { durationToken, seconds } from '../src/lib/motion'
import { chipLabel, parseQuery, removeChip, withFilter } from '../src/lib/query'
import { installHotkeys } from '../src/lib/hotkeys'
import { ICONS, resolveIcon } from '../src/lib/icons'
import { isActive, NAV_GROUPS } from '../src/lib/nav'

describe('parseQuery', () => {
  it('separa filtri, negazioni e testo libero', () => {
    const q = parseQuery('label:moto -color:nero "palco rosso" ducati iso:100-800')
    expect(q.chips.map((c) => [c.key, c.value, c.negate])).toEqual([['label', 'moto', false], ['color', 'nero', true], ['iso', '100-800', false]])
    expect(q.text).toBe('palco rosso ducati')
  })

  it('riconosce gli alias italiani e lascia come testo le chiavi sconosciute', () => {
    const q = parseQuery('anno:2023 etichetta:moto orario:5')
    expect(q.chips.map((c) => c.key)).toEqual(['year', 'label'])
    expect(q.text).toBe('orario:5')
  })

  it('riconosce formato e dimensioni, anche con gli alias italiani', () => {
    const q = parseQuery('formato:4:3 megapixel:12 larghezza:4000+ peso:2-6 ratio:16:9')
    expect(q.chips.map((c) => [c.key, c.value])).toEqual([
      ['ratio', '4:3'], ['mp', '12'], ['width', '4000+'], ['mb', '2-6'], ['ratio', '16:9'],
    ])
    expect(q.text).toBe('')
  })

  it('gestisce valori tra virgolette e query vuote', () => {
    expect(parseQuery('person:"Anna Rossi"').chips[0].value).toBe('Anna Rossi')
    expect(parseQuery('')).toEqual({ chips: [], text: '' })
    expect(parseQuery('   ').chips).toEqual([])
  })

  it('toglie e sostituisce i filtri senza toccare il resto', () => {
    const q = 'moto rossa label:moto year:2024'
    const chip = parseQuery(q).chips[0]
    expect(removeChip(q, chip)).toBe('moto rossa year:2024')
    expect(withFilter(q, 'year', '2023')).toBe('moto rossa label:moto year:2023')
    expect(withFilter('', 'person', 'Anna Rossi')).toBe('person:"Anna Rossi"')
    expect(chipLabel(parseQuery('-color:nero').chips[0])).toBe('−color: nero')
  })
})

describe('layout giustificato', () => {
  const items = Array.from({ length: 23 }, (_, i) => ({ width: 1000 + (i % 5) * 300, height: 800 + (i % 3) * 200 }))

  it('riempie la larghezza in ogni riga tranne l\'ultima', () => {
    const rows = justify(items, 1000, 200, 4)
    expect(rows.length).toBeGreaterThan(1)
    for (const row of rows.slice(0, -1)) {
      const used = row.tiles.reduce((s, t) => s + t.width, 0) + 4 * (row.tiles.length - 1)
      expect(used).toBeGreaterThan(1000 - row.tiles.length - 4)
      expect(used).toBeLessThanOrEqual(1000)
    }
    expect(rows.flatMap((r) => r.tiles).map((t) => t.index)).toEqual(items.map((_, i) => i))
  })

  it('non allunga l\'ultima riga oltre l\'altezza di riferimento', () => {
    const rows = justify([{ width: 1500, height: 1000 }], 1000, 200, 4)
    expect(rows).toHaveLength(1)
    expect(rows[0].height).toBeLessThanOrEqual(200)
  })

  it('gestisce casi limite e proporzioni mancanti', () => {
    expect(justify([], 1000, 200, 4)).toEqual([])
    expect(justify(items, 0, 200, 4)).toEqual([])
    expect(aspect({ width: 0, height: 0 })).toBe(1.5)
    expect(aspect({ width: 10000, height: 100 })).toBe(3.5)
    expect(aspect({ width: 100, height: 10000 })).toBe(0.4)
  })
})

describe('formati', () => {
  it('formatta durate, dimensioni e date', () => {
    expect(formatDuration(65)).toBe('1:05')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(formatDuration(-3)).toBe('0:00')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1,5 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5,0 MB')
    expect(formatDate(null)).toBe('—')
    expect(formatDate('boh')).toBe('—')
    expect(formatDate('2024-05-01T10:00:00')).toContain('2024')
  })

  it('raggruppa le ricerche recenti come la sidebar di sift', () => {
    const now = new Date('2024-05-10T15:00:00').getTime()
    const day = 86_400_000
    const items = [
      { q: 'oggi', ts: now - 3_600_000, pinned: false }, { q: 'ieri', ts: now - day, pinned: false },
      { q: 'settimana', ts: now - 4 * day, pinned: false }, { q: 'vecchia', ts: now - 30 * day, pinned: false },
      { q: 'fissata', ts: now - 40 * day, pinned: true },
    ]
    const groups = groupHistory(items, now)
    expect(groups.map((g) => g.title)).toEqual(['Fissate', 'Oggi', 'Ieri', 'Questa settimana', 'Prima'])
    expect(groups.map((g) => g.items[0].q)).toEqual(['fissata', 'oggi', 'ieri', 'settimana', 'vecchia'])
    expect(groupHistory([], now)).toEqual([])
  })
})

describe('movimento', () => {
  it('legge le durate dai token CSS (anche in secondi) con ripiego sul valore di sift', () => {
    document.documentElement.style.setProperty('--dur-base', '300ms')
    document.documentElement.style.setProperty('--dur-slow', '0.5s')
    expect(durationToken('--dur-base')).toBe(300)
    expect(durationToken('--dur-slow')).toBe(500)
    expect(seconds('--dur-base')).toBe(0.3)
    expect(durationToken('--dur-fast')).toBe(140)
    expect(durationToken('--sconosciuto')).toBe(0)
    document.documentElement.style.setProperty('--dur-base', '0ms')
    expect(seconds('--dur-base')).toBe(0)
  })
})

describe('scorciatoie', () => {
  function press(key: string, init: KeyboardEventInit = {}, target: Element = document.body): void {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init }))
  }

  it('/ cerca, ? mostra l\'elenco, g poi lettera naviga', () => {
    const h = { focusSearch: vi.fn(), showHelp: vi.fn(), go: vi.fn() }
    const off = installHotkeys(h)
    press('/')
    press('?')
    press('g')
    press('a')
    expect(h.focusSearch).toHaveBeenCalledOnce()
    expect(h.showHelp).toHaveBeenCalledOnce()
    expect(h.go).toHaveBeenCalledWith('/albums')
    off()
    press('/')
    expect(h.focusSearch).toHaveBeenCalledOnce()
  })

  it('non scatta mentre si digita né con i modificatori e la sequenza scade dopo 1 s', () => {
    vi.useFakeTimers()
    const h = { focusSearch: vi.fn(), showHelp: vi.fn(), go: vi.fn() }
    const off = installHotkeys(h)
    const input = document.createElement('input')
    document.body.append(input)
    press('/', {}, input)
    press('/', { ctrlKey: true })
    expect(h.focusSearch).not.toHaveBeenCalled()
    press('g')
    vi.advanceTimersByTime(1100)
    press('a')
    expect(h.go).not.toHaveBeenCalled()
    press('g')
    press('x')  // lettera senza destinazione: nessuna navigazione e la sequenza si chiude
    press('a')
    expect(h.go).not.toHaveBeenCalled()
    off()
  })
})

describe('icone e navigazione', () => {
  it('ogni icona usata dalla navigazione esiste e il ripiego è LayoutGrid', () => {
    for (const group of NAV_GROUPS) for (const item of group.items) expect(item.icon in ICONS).toBe(true)
    expect(resolveIcon('inesistente')).toBe(ICONS.grid)
  })

  it('la voce attiva è una sola e Esplora vale solo per la radice', () => {
    const all = NAV_GROUPS.flatMap((g) => g.items)
    expect(all.filter((i) => isActive(i, '/albums/a123')).map((i) => i.label)).toEqual(['Album'])
    expect(all.filter((i) => isActive(i, '/')).map((i) => i.label)).toEqual(['Esplora'])
    expect(all.filter((i) => isActive(i, '/people/s1')).map((i) => i.label)).toEqual(['Persone'])
  })
})
