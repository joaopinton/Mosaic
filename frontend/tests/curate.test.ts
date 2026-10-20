import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ photos: vi.fn(), curate: vi.fn(), createAlbum: vi.fn(), album: vi.fn(), patchAlbum: vi.fn(),
  captionAlbum: vi.fn(), deleteAlbum: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import CuratePanel from '../src/components/CuratePanel.vue'
import PhotoGrid from '../src/components/PhotoGrid.vue'
import AlbumPage from '../src/pages/AlbumPage.vue'
import SelectionPage from '../src/pages/SelectionPage.vue'
import { DEFAULT_PARAMS, filterQuery, formatSpan, fromRouteQuery, toRouteQuery } from '../src/lib/curate'
import * as store from '../src/lib/store'
import type { Album, CurateResult, PhotoSummary } from '../src/lib/types'

const album = (extra: Partial<Album> = {}): Album => ({
  uid: 'a1', type: 'folder', slug: 'festa', title: 'Festa', description: '', smart: true, filter: '', favorite: false,
  private: false, photo_count: 40, cover: null, ...extra,
})
const photo = (n: number): PhotoSummary => ({
  uid: `p${n}`, type: 'image', title: '', caption: '', taken_at_local: `2024-05-01T1${n}:00:00`, width: 640, height: 480,
  duration: 0, main_color: '', quality: 5, hash: `h${n}`, favorite: false, private: false, archived: false,
})
const result = (n = 3): CurateResult => ({
  album: album(),
  photos: Array.from({ length: n }, (_, i) => ({ ...photo(i), reasons: i === 0 ? ['Prima foto con Anna'] : [] })),
  stats: { candidates: 40, selected: n, rejected: 5, not_chosen: 32, people_total: 4, people_covered: 3,
    people: [{ name: 'Anna', count: 2 }], start: '2024-05-01T10:00:00', end: '2024-05-01T18:30:00',
    timeline: [[10, 1], [0, 0], [30, 2]], ai_ready: false },
})

const mounted: { unmount: () => void }[] = []
beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
  store.sidebar.panel = null
  store.curate.source = null
  store.curate.draft = null
  store.clearSelection()
})
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

async function routerAt(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/albums/:uid', component: AlbumPage }, { path: '/albums/:uid/selection', component: SelectionPage },
  ] })
  await router.push(path)
  await router.isReady()
  return router
}

describe('parametri della selezione', () => {
  it('costruisce la stessa query del server', () => {
    expect(filterQuery('a1', { ...DEFAULT_PARAMS, shape: '4:5' })).toBe('album:a1 ratio:4:5 photo:true')
    expect(filterQuery('a1', { ...DEFAULT_PARAMS, shape: 'portrait', videos: true, text: ' palco ' })).toBe('album:a1 portrait:true palco')
  })

  it('va e torna dall\'indirizzo, scartando i valori non validi', () => {
    const p = { ...DEFAULT_PARAMS, count: 36, shape: '3:2', text: 'torta', mode: 'variety' as const, people: false, videos: true, seed: 42 }
    expect(fromRouteQuery(toRouteQuery(p))).toEqual(p)
    expect(toRouteQuery(DEFAULT_PARAMS)).toEqual({ n: '24' })
    expect(fromRouteQuery({ n: '9999', shape: 'tondo', mode: 'boh', seed: '-3' })).toEqual({ ...DEFAULT_PARAMS, count: 200 })
    expect(fromRouteQuery({ n: 'abc' }).count).toBe(24)
  })

  it('descrive la durata dell\'evento', () => {
    expect(formatSpan('2024-05-01T10:00:00', '2024-05-01T18:30:00')).toBe('1 maggio 2024, 10:00–18:30')
    expect(formatSpan('2024-05-01T10:00:00', '2024-05-03T09:00:00')).toBe('1 maggio 2024 – 3 maggio 2024')
    expect(formatSpan(null, null)).toBe('')
  })
})

describe('Crea selezione', () => {
  it('il tasto nell\'header apre il pannello per quell\'album', async () => {
    api.album.mockResolvedValue(album())
    const router = await routerAt('/albums/a1')
    const wrapper = mount(AlbumPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } } })
    mounted.push(wrapper)
    await flushPromises()
    await wrapper.get('[data-test="curate"]').trigger('click')
    expect(store.sidebar.panel).toBe('curate')
    expect(store.curate.source).toEqual({ uid: 'a1', title: 'Festa' })
  })

  it('il pannello conta le foto adatte e apre la selezione con quantità e formato', async () => {
    vi.useFakeTimers()
    api.photos.mockResolvedValue({ total: 37, semantic: false, photos: [] })
    store.openCurate({ uid: 'a1', title: 'Festa' })
    const router = await routerAt('/albums/a1')
    const push = vi.spyOn(router, 'push')
    const wrapper = mount(CuratePanel, { global: { plugins: [router] } })
    mounted.push(wrapper)
    await vi.advanceTimersByTimeAsync(400)
    expect(wrapper.text()).toContain('37 foto adatte')
    await wrapper.findAll('button').find((b) => b.text() === '50')!.trigger('click')
    await wrapper.findAll('label').find((l) => l.text() === 'Verticale')!.get('input').setValue(true)
    await vi.advanceTimersByTimeAsync(400)
    expect(api.photos).toHaveBeenLastCalledWith({ q: 'album:a1 portrait:true photo:true', count: 1 })
    expect(wrapper.text()).toContain('le prendo tutte')
    await wrapper.get('form').trigger('submit')
    expect(push).toHaveBeenCalledWith({ path: '/albums/a1/selection', query: { n: '50', shape: 'portrait' } })
    expect(store.sidebar.panel).toBeNull()
  })

  it('la pagina mostra la selezione, le persone e la salva come album', async () => {
    api.curate.mockResolvedValue(result())
    api.createAlbum.mockResolvedValue(album({ uid: 'n1', type: 'album', title: 'Selezione di Festa', photo_count: 3 }))
    const router = await routerAt('/albums/a1/selection?n=3&shape=4:3')
    const wrapper = mount(SelectionPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } }, attachTo: document.body })
    mounted.push(wrapper)
    await flushPromises()
    expect(api.curate).toHaveBeenCalledWith('a1', { ...DEFAULT_PARAMS, count: 3, shape: '4:3' })
    expect(wrapper.text()).toContain('3 foto scelte su 40')
    expect(wrapper.text()).toContain('Anna · 2')
    expect(wrapper.text()).toContain('+2 senza nome')
    expect(wrapper.text()).toContain('5 scartate')
    expect(wrapper.text()).toContain('analisi AI non è ancora finita')
    await wrapper.findAll('button').find((b) => b.text().includes('Seleziona tutte'))!.trigger('click')
    expect(store.selected.value).toEqual(['p0', 'p1', 'p2'])
    await wrapper.get('[data-test="save-selection"]').trigger('click')
    expect((document.getElementById('sel-title') as HTMLInputElement).value).toBe('Selezione di Festa')
    const create = [...document.querySelectorAll('.dlg button')].find((b) => b.textContent?.trim() === 'Salva') as HTMLButtonElement
    create.click()
    await flushPromises()
    expect(api.createAlbum).toHaveBeenCalledWith('Selezione di Festa', ['p0', 'p1', 'p2'], 'a1')  // salvata dentro l'album di partenza
    expect(router.currentRoute.value.path).toBe('/albums/a1')
  })

  it('aprire una foto della selezione non la ricalcola e non perde le foto selezionate', async () => {
    api.curate.mockResolvedValue(result())
    const router = await routerAt('/albums/a1/selection?n=3')
    const wrapper = mount(SelectionPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } } })
    mounted.push(wrapper)
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Seleziona tutte'))!.trigger('click')
    await router.push({ query: { ...router.currentRoute.value.query, photo: 'p1' } })
    await flushPromises()
    expect(api.curate).toHaveBeenCalledTimes(1)
    expect(store.selected.value).toEqual(['p0', 'p1', 'p2'])
  })

  it('rigenera con un seme nuovo e riapre il pannello con gli stessi valori', async () => {
    api.curate.mockResolvedValue(result())
    const router = await routerAt('/albums/a1/selection?n=3&mode=variety')
    const wrapper = mount(SelectionPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } } })
    mounted.push(wrapper)
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('Rigenera'))!.trigger('click')
    await flushPromises()
    expect(Number(router.currentRoute.value.query.seed)).toBeGreaterThan(0)
    expect(api.curate).toHaveBeenCalledTimes(2)
    await wrapper.findAll('button').find((b) => b.text().includes('Modifica'))!.trigger('click')
    expect(store.sidebar.panel).toBe('curate')
    expect(store.curate.draft).toMatchObject({ count: 3, mode: 'variety' })
  })

  it('la griglia mostra un elenco già pronto senza chiedere nulla al server', async () => {
    const router = await routerAt('/albums/a1/selection')
    const items = [photo(1), photo(2), photo(3)]
    const wrapper = mount(PhotoGrid, { props: { items, hints: { p1: 'Preferita' } }, global: { plugins: [router] } })
    mounted.push(wrapper)
    await flushPromises()
    expect(api.photos).not.toHaveBeenCalled()
    expect(wrapper.findAll('.tile')).toHaveLength(3)
    expect(wrapper.get('.tile').attributes('title')).toBe('Preferita')
    expect(wrapper.emitted('loaded')?.[0]).toEqual([3, false])
  })

  it('un album che non esiste più mostra il messaggio giusto', async () => {
    const { ApiError } = await import('../src/lib/api')
    api.curate.mockRejectedValue(new ApiError(404, 'album non trovato'))
    const router = await routerAt('/albums/zz/selection')
    const wrapper = mount(SelectionPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } } })
    mounted.push(wrapper)
    await flushPromises()
    expect(wrapper.text()).toContain('Questo album non esiste più')
  })
})

describe('cartelle, momenti e lavori salvati', () => {
  it('la sidebar segue il tipo di album aperto, non sempre «Album»', async () => {
    const { isActive, SECTION_OF } = await import('../src/lib/nav')
    const folders = { to: '/folders', label: 'Cartelle', icon: 'folder' }
    const albumsItem = { to: '/albums', label: 'Album', icon: 'album' }
    expect(isActive(albumsItem, '/albums/a1', SECTION_OF.folder)).toBe(false)
    expect(isActive(folders, '/albums/a1', SECTION_OF.folder)).toBe(true)
    expect(isActive(albumsItem, '/albums/a1', SECTION_OF.album)).toBe(true)
    expect(isActive(albumsItem, '/albums', null)).toBe(true)
  })

  it('la pagina della cartella imposta la sezione Cartelle', async () => {
    api.album.mockResolvedValue(album())
    const router = await routerAt('/albums/a1')
    const wrapper = mount(AlbumPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } } })
    mounted.push(wrapper)
    await flushPromises()
    expect(store.currentSection.value).toBe('/folders')
    wrapper.unmount()
    expect(store.currentSection.value).toBeNull()
  })
})
