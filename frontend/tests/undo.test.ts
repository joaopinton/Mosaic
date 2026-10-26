import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Ctrl+A, Ctrl+Z / Ctrl+Y, selezione a intervalli, annulla delle azioni di gruppo e download zip (D-36).
const api = vi.hoisted(() => ({
  batch: vi.fn(), albums: vi.fn(), photos: vi.fn(), exportPhotos: vi.fn(), exportDefaultDir: vi.fn(), createAlbum: vi.fn(), deleteAlbum: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import ExportDialog from '../src/components/ExportDialog.vue'
import SelectionBar from '../src/components/SelectionBar.vue'
import { runBatch } from '../src/lib/batch'
import { installHotkeys } from '../src/lib/hotkeys'
import * as store from '../src/lib/store'
import { toasts } from '../src/lib/toast'
import { clearUndo, pushUndo, redo, undo } from '../src/lib/undo'

const photo = (uid: string, over: Record<string, unknown> = {}) =>
  ({ uid, type: 'image', title: '', hash: null, favorite: false, private: false, taken_at_local: null, duration: null, ...over }) as never

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.albums.mockResolvedValue([])
  api.batch.mockResolvedValue({ changed: 2 })
  toasts.value = []
  clearUndo()
  store.clearSelection()
  store.viewer.photos = []
  store.gridSource.value = null
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('selezione', () => {
  it('Maiusc+clic seleziona l\'intervallo dall\'ultima foto scelta', () => {
    const all = ['a', 'b', 'c', 'd', 'e']
    store.toggleSelected('b')
    store.selectRange('d', all)
    expect(store.selected.value.sort()).toEqual(['b', 'c', 'd'])
    store.selectRange('a', all)
    expect(store.selected.value.sort()).toEqual(['a', 'b', 'c', 'd'])
  })

  it('Ctrl+A seleziona anche le foto non ancora caricate', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ photos: [photo('a'), photo('b'), photo('c')], total: 3, semantic: false }))))
    store.gridSource.value = { query: 'album:x' }
    expect(await store.selectAllInView()).toBe(3)
    expect(store.selected.value).toEqual(['a', 'b', 'c'])
    store.gridSource.value = { query: '', items: ['x', 'y'] }
    await store.selectAllInView()
    expect(store.selected.value).toEqual(['x', 'y'])
  })
})

describe('scorciatoie', () => {
  const handlers = () => ({ focusSearch: vi.fn(), showHelp: vi.fn(), go: vi.fn(), selectAll: vi.fn(), undo: vi.fn(), redo: vi.fn(), escape: vi.fn() })
  const press = (init: KeyboardEventInit, target: EventTarget = window) => {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
    target.dispatchEvent(event)
    return event
  }

  it('Ctrl+A, Ctrl+Z, Ctrl+Y e Ctrl+Maiusc+Z chiamano le azioni giuste', () => {
    const h = handlers()
    const off = installHotkeys(h)
    expect(press({ key: 'a', ctrlKey: true }).defaultPrevented).toBe(true)
    press({ key: 'z', ctrlKey: true })
    press({ key: 'y', ctrlKey: true })
    press({ key: 'Z', ctrlKey: true, shiftKey: true })
    press({ key: 'z', metaKey: true })
    press({ key: 'Escape' })
    off()
    expect(h.selectAll).toHaveBeenCalledTimes(1)
    expect(h.undo).toHaveBeenCalledTimes(2)
    expect(h.redo).toHaveBeenCalledTimes(2)
    expect(h.escape).toHaveBeenCalledTimes(1)
  })

  it('dentro un campo di testo valgono le scorciatoie del campo', () => {
    const h = handlers()
    const off = installHotkeys(h)
    const input = document.createElement('input')
    document.body.append(input)
    expect(press({ key: 'a', ctrlKey: true }, input).defaultPrevented).toBe(false)
    press({ key: 'z', ctrlKey: true }, input)
    off()
    input.remove()
    expect(h.selectAll).not.toHaveBeenCalled()
    expect(h.undo).not.toHaveBeenCalled()
  })
})

describe('annulla e ripeti', () => {
  it('Ctrl+Z annulla l\'ultima azione e Ctrl+Y la rifà', async () => {
    const log: string[] = []
    pushUndo({ label: 'uno', undo: async () => void log.push('-1'), redo: async () => void log.push('+1') })
    pushUndo({ label: 'due', undo: async () => void log.push('-2'), redo: async () => void log.push('+2') })
    expect(await undo()).toBe(true)
    expect(await undo()).toBe(true)
    expect(await undo()).toBe(false)
    expect(await redo()).toBe(true)
    expect(log).toEqual(['-2', '-1', '+1'])
    pushUndo({ label: 'tre', undo: async () => undefined, redo: async () => undefined })
    expect(await redo()).toBe(false) // una nuova azione azzera il «ripeti»
  })

  it('se annullare fallisce lo dice e la voce resta', async () => {
    pushUndo({ label: 'rotta', undo: async () => { throw new Error('server giù') }, redo: async () => undefined })
    expect(await undo()).toBe(false)
    expect(toasts.value.at(-1)?.text).toContain('server giù')
    api.batch.mockResolvedValue({ changed: 1 })
  })

  it('un\'azione di gruppo si annulla con il suo contrario e si rifà', async () => {
    store.viewer.photos = [photo('a', { favorite: true }), photo('b')]
    const out = await runBatch(['a', 'b'], 'favorite', 'Aggiunte ai preferiti')
    expect(out.undoable).toBe(true)
    expect(toasts.value.at(-1)?.action?.label).toBe('Annulla')
    await undo()
    expect(api.batch).toHaveBeenLastCalledWith(['b'], 'unfavorite', undefined) // «a» era già preferita: non si tocca
    await redo()
    expect(api.batch).toHaveBeenLastCalledWith(['b'], 'favorite', undefined)
  })

  it('archivia e ripristina sono uno il contrario dell\'altro; l\'album non si annulla se il server non dice quali foto', async () => {
    await runBatch(['a'], 'archive', 'Archiviate')
    await undo()
    expect(api.batch).toHaveBeenLastCalledWith(['a'], 'restore', undefined)
    api.batch.mockResolvedValue({ changed: 1 })
    const partial = await runBatch(['a', 'b'], 'add_to_album', 'Aggiunte', 'alb')
    expect(partial.undoable).toBe(false)
  })
})

describe('barra della selezione', () => {
  it('se sono tutte preferite il pulsante le toglie dai preferiti', async () => {
    store.viewer.photos = [photo('a', { favorite: true }), photo('b', { favorite: true })]
    store.selected.value = ['a', 'b']
    const w = mount(SelectionBar, { attachTo: document.body })
    await flushPromises()
    const fav = w.get('[data-test="bar-favorite"]')
    expect(fav.text()).toContain('Non preferite')
    await fav.trigger('click')
    await flushPromises()
    expect(api.batch).toHaveBeenCalledWith(['a', 'b'], 'unfavorite', undefined)
    expect(store.hasSelection.value).toBe(false)
    w.unmount()
  })
})

describe('download zip', () => {
  it('«Scarica» manda zip e opzioni, poi scarica lo zip pronto', async () => {
    api.exportDefaultDir.mockResolvedValue({ folder: 'C:\\x' })
    api.exportPhotos.mockResolvedValue({ job: 9, folder: '' })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const w = mount(ExportDialog, { props: { open: true, uids: ['p1'], title: 'Gita: 2026', mode: 'zip' }, attachTo: document.body })
    await flushPromises()
    expect(w.find('[data-test="export-dest"]').exists()).toBe(false)
    expect(w.get('[data-test="export-zip-hint"]').text()).toContain('Gita 2026.zip')
    await w.get('[data-test="export-numbered"]').setValue(true)
    await w.get('[data-test="export-start"]').trigger('click')
    await flushPromises()
    expect(api.exportPhotos).toHaveBeenCalledWith(expect.objectContaining({ zip: true, dest: '', zip_name: 'Gita 2026', numbered: true, fmt: 'original' }))
    store.jobs.value = [{ id: 9, type: 'export', state: 'done', progress: 1, message: '1 foto pronte da scaricare', error: '', created_at: '', finished_at: '', done: 1, total: 1 }]
    await flushPromises()
    expect(click).toHaveBeenCalled()
    expect(w.find('[data-test="export-download"]').exists()).toBe(true)
    expect(JSON.parse(localStorage.getItem('mosaic.export.prefs') ?? '{}').numbered).toBe(true) // le scelte si ricordano
    w.unmount()
    store.jobs.value = []
  })
})
