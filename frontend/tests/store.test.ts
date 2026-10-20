import { beforeEach, describe, expect, it, vi } from 'vitest'
import { lintContrast, lintCss, lintProject, styleOf } from '../scripts/lint-tokens.mjs'

async function freshStore() {
  vi.resetModules()
  return await import('../src/lib/store')
}

describe('ricerche recenti', () => {
  beforeEach(() => localStorage.clear())

  it('toglie i duplicati, tiene il fissaggio, rispetta il limite e persiste', async () => {
    const s = await freshStore()
    s.pushHistory('moto')
    s.pushHistory('auto')
    s.updateHistory('moto', { pinned: true, title: 'Le mie moto' })
    s.pushHistory('moto')
    expect(s.history.value.map((h) => h.q)).toEqual(['moto', 'auto'])
    expect(s.history.value[0]).toMatchObject({ pinned: true, title: 'Le mie moto' })
    s.pushHistory('   ')
    expect(s.history.value).toHaveLength(2)
    for (let i = 0; i < 60; i++) s.pushHistory(`q${i}`)
    expect(s.history.value).toHaveLength(40)
    s.removeHistory('q59')
    expect(JSON.parse(localStorage.getItem('mosaic.history') ?? '[]')).toHaveLength(39)
    expect((await freshStore()).history.value).toHaveLength(39)
  })

  it('ignora una cronologia salvata corrotta', async () => {
    localStorage.setItem('mosaic.history', '{rotto')
    expect((await freshStore()).history.value).toEqual([])
    localStorage.setItem('mosaic.history', JSON.stringify([{ q: 1 }, { q: 'ok', ts: 1, pinned: false }]))
    expect((await freshStore()).history.value.map((h) => h.q)).toEqual(['ok'])
  })
})

describe('selezione e sidebar', () => {
  it('seleziona, deseleziona e svuota', async () => {
    const s = await freshStore()
    s.toggleSelected('a'); s.toggleSelected('b'); s.toggleSelected('a')
    expect(s.selected.value).toEqual(['b'])
    expect(s.hasSelection.value).toBe(true)
    s.clearSelection()
    expect(s.hasSelection.value).toBe(false)
  })

  it('ricorda la scelta della sidebar e ignora valori non validi', async () => {
    localStorage.setItem('mosaic.sidebar', 'collapsed')
    expect((await freshStore()).sidebar.preference).toBe('collapsed')
    localStorage.setItem('mosaic.sidebar', 'boh')
    const s = await freshStore()
    expect(s.sidebar.preference).toBeNull()
    s.setSidebarPreference('open')
    expect(localStorage.getItem('mosaic.sidebar')).toBe('open')
  })
})

describe('eventi dei job (SSE)', () => {
  it('aggiorna i job e fa ricaricare le griglie quando uno finisce', async () => {
    class FakeSource {
      static last: FakeSource
      onmessage: ((m: { data: string }) => void) | null = null
      closed = false
      constructor() { FakeSource.last = this }
      close() { this.closed = true }
    }
    vi.stubGlobal('EventSource', FakeSource)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ photos: 1 }), { status: 200 })))
    const s = await freshStore()
    const stop = s.startEvents()
    const send = (e: object) => FakeSource.last.onmessage?.({ data: JSON.stringify(e) })
    send({ event: 'job', id: 1, type: 'index', state: 'running', progress: 0.4, message: '10 file' })
    expect(s.jobs.value[0]).toMatchObject({ id: 1, state: 'running', progress: 0.4 })
    expect(s.isBusy.value).toBe(true)
    const before = s.libraryVersion.value
    send({ event: 'job', id: 1, type: 'index', state: 'done', progress: 1 })
    expect(s.jobs.value).toHaveLength(1)
    expect(s.jobs.value[0].state).toBe('done')
    expect(s.libraryVersion.value).toBe(before + 1)
    expect(s.isBusy.value).toBe(false)
    FakeSource.last.onmessage?.({ data: 'non json' })
    send({ event: 'altro' })
    stop()
    expect(FakeSource.last.closed).toBe(true)
    vi.unstubAllGlobals()
  })
})

describe('notifiche', () => {
  it('mostra al massimo tre avvisi e li chiude da soli', async () => {
    vi.useFakeTimers()
    const t = await import('../src/lib/toast')
    const ids = [1, 2, 3, 4].map((n) => t.toast(`messaggio ${n}`))
    expect(t.toasts.value.map((x) => x.text)).toEqual(['messaggio 2', 'messaggio 3', 'messaggio 4'])
    t.dismiss(ids[3])
    expect(t.toasts.value).toHaveLength(2)
    vi.advanceTimersByTime(5100)
    expect(t.toasts.value).toHaveLength(0)
    expect(t.errorMessage(new Error('x'))).toBe('x')
    expect(t.errorMessage('boh')).toBe('Errore imprevisto')
  })
})

describe('lint dei token', () => {
  it('riconosce le violazioni', () => {
    const bad = lintCss(`.a { color: #fff; background: rgb(1,2,3); transition: color 200ms; border-color: var(--brand-600); background: white; }`)
    expect(bad.map((v) => v.rule).sort()).toEqual(
      ['colore con nome', 'colore letterale', 'durata letterale in transition', 'primitivo di palette'].sort())
    expect(lintCss('.a { transition: color var(--dur-fast) var(--ease-out); color: var(--fg-default); }')).toEqual([])
    expect(lintCss('/* #fff in un commento */ .a { color: var(--fg-default); }')).toEqual([])
    expect(styleOf('<template/><style scoped>.a{color:#fff}</style>', 'x.vue')).toBe('.a{color:#fff}')
  })

  it('il codice del progetto rispetta la regola "solo token"', () => {
    expect(lintProject()).toEqual([])
  })

  it('i token rispettano il contrasto in chiaro e in scuro', () => {
    expect(lintContrast()).toEqual([])
  })

  it('il controllo di contrasto segnala una coppia illeggibile e uno scrim troppo trasparente', () => {
    const weakText = lintContrast({ append: ':root { --fg-default: light-dark(var(--neutral-400), var(--neutral-600)); }' })
    expect(weakText.some((v) => v.text.startsWith('--fg-default su --bg-surface (light)'))).toBe(true)
    const weakScrim = lintContrast({ append: ':root { --overlay-scrim: oklch(0% 0 0 / 0.1); }' })
    expect(weakScrim.some((v) => v.text.includes('sopra bianco'))).toBe(true)
  })
})
