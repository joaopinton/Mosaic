import { flushPromises, mount } from '@vue/test-utils'
import axe from 'axe-core'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mockMatchMedia } from './setup'

const mounted: { unmount: () => void }[] = []
const track = <T extends { unmount: () => void }>(wrapper: T): T => {
  mounted.push(wrapper)
  return wrapper
}

const api = vi.hoisted(() => ({
  status: vi.fn(), jobs: vi.fn(), albums: vi.fn(), photos: vi.fn(), suggest: vi.fn(), photo: vi.fn(), similar: vi.fn(),
  labels: vi.fn(), subjects: vi.fn(), faces: vi.fn(), batch: vi.fn(), createAlbum: vi.fn(), suggestions: vi.fn(),
  confirmMarker: vi.fn(), rejectSuggestion: vi.fn(), confirmSuggestions: vi.fn(), nameFace: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import AppShell from '../src/components/AppShell.vue'
import Dialog from '../src/components/Dialog.vue'
import PhotoGrid from '../src/components/PhotoGrid.vue'
import PeoplePage from '../src/pages/PeoplePage.vue'
import Segmented from '../src/components/Segmented.vue'
import Sidebar from '../src/components/Sidebar.vue'
import * as store from '../src/lib/store'
import type { PhotoSummary } from '../src/lib/types'

const photo = (n: number, extra: Partial<PhotoSummary> = {}): PhotoSummary => ({
  uid: `p${n}`, type: 'image', title: `Foto ${n}`, caption: '', taken_at_local: '2024-05-01T10:00:00', width: 1500, height: 1000,
  duration: 0, main_color: 'Rosso', quality: 3, hash: `${String(n).repeat(40)}`.slice(0, 40), favorite: false, private: false,
  archived: false, ...extra,
})

function makeRouter(path = '/') {
  const page = { template: '<div />' }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:rest(.*)*', component: page }] })
  void router.push(path)
  return router
}

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.labels.mockResolvedValue([])
  api.subjects.mockResolvedValue([])
  api.faces.mockResolvedValue([])
  api.suggestions.mockResolvedValue([])
  api.status.mockResolvedValue({ first_run: false, library_roots: ['D:/Foto'], photos: 3, videos: 0, analyzed: 3, people: 0, albums: 0,
    jobs: {}, busy: false, assets: {}, ai_ready: true, llm_ready: false, rename_auto: true, delete_mode: 'catalog' })
  api.jobs.mockResolvedValue([])
  api.albums.mockResolvedValue([])
  api.suggest.mockResolvedValue({ keys: [], values: [] })
  api.photos.mockResolvedValue({ total: 3, semantic: false, photos: [photo(1), photo(2), photo(3)] })
  store.history.value = []
  store.clearSelection()
  store.sidebar.preference = null
  store.sidebar.panel = null
  mockMatchMedia(() => true)
})

describe('Segmented', () => {
  const options = [{ value: 'a', label: 'Uno' }, { value: 'b', label: 'Due' }]

  it('espone un gruppo radio accessibile e segnala la scelta', async () => {
    const wrapper = track(mount(Segmented, { props: { modelValue: 'a', options, label: 'Elenco' }, attachTo: document.body }))
    const group = wrapper.get('[role="radiogroup"]')
    expect(group.attributes('aria-label')).toBe('Elenco')
    const radios = wrapper.findAll('input[type="radio"]')
    expect(radios).toHaveLength(2)
    expect((radios[0].element as HTMLInputElement).checked).toBe(true)
    await radios[1].trigger('change')
    expect(wrapper.emitted('update:modelValue')).toEqual([['b']])
    expect(wrapper.findAll('.segment.on')).toHaveLength(1)
  })
})

describe('Dialog', () => {
  it('monta il contenuto solo da aperto e chiude con Esc o clic sullo sfondo', async () => {
    const wrapper = track(mount(Dialog, { props: { open: false, title: 'Titolo' }, slots: { default: '<p class="inside">dentro</p>' }, attachTo: document.body }))
    expect(wrapper.find('.inside').exists()).toBe(false)
    await wrapper.setProps({ open: true })
    await nextTick()
    await nextTick()
    const dialog = wrapper.get('dialog')
    expect(dialog.attributes('open')).toBeDefined()
    expect(wrapper.get('.inside').text()).toBe('dentro')
    expect(wrapper.get('h2').text()).toBe('Titolo')
    await wrapper.get('.inside').trigger('click')
    expect(wrapper.emitted('close')).toBeUndefined()
    await dialog.trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    const cancel = new Event('cancel', { cancelable: true })
    dialog.element.dispatchEvent(cancel)
    expect(cancel.defaultPrevented).toBe(true)
    expect(wrapper.emitted('close')).toHaveLength(2)
    await wrapper.get('button[aria-label="Chiudi"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(3)
  })
})

describe('Sidebar', () => {
  async function mountSidebar(props: { collapsed: boolean }, path = '/') {
    const router = makeRouter(path)
    await router.isReady()
    return { router, wrapper: track(mount(Sidebar, { props, global: { plugins: [router] }, attachTo: document.body })) }
  }

  it('mostra tre gruppi di navigazione e una sola voce attiva', async () => {
    const { wrapper } = await mountSidebar({ collapsed: false }, '/albums/a123')
    const titles = wrapper.findAll('h2.caps').map((h) => h.text())
    expect(titles).toEqual(['Libreria', 'Tipi', 'Stato'])
    const links = wrapper.findAll('a.navItem')
    expect(links).toHaveLength(14)
    const current = links.filter((l) => l.attributes('aria-current') === 'page')
    expect(current.map((l) => l.text())).toEqual(['Album'])
    expect(wrapper.text()).toContain('Le ricerche che avvii compaiono qui.')
    expect(wrapper.get('a.newBtn').attributes('href')).toBe('/library')
  })

  it('compressa: colonna di pulsanti quadrati con nome accessibile per ogni voce', async () => {
    const { wrapper } = await mountSidebar({ collapsed: true })
    expect(wrapper.find('h2.caps').exists()).toBe(false)
    const rail = wrapper.findAll('.rail a, .rail button')
    expect(rail.length).toBeGreaterThanOrEqual(15)
    for (const control of rail) expect(control.attributes('aria-label')).toBeTruthy()
    expect(wrapper.findAll('.tip-text').length).toBeGreaterThan(10)
    expect(wrapper.find('.userText').exists()).toBe(false)
  })

  it('"Cerca" apre il campo, Invio porta ai risultati e salva la ricerca', async () => {
    const { wrapper, router } = await mountSidebar({ collapsed: false })
    await wrapper.findAll('button.navItem').find((b) => b.text() === 'Cerca')!.trigger('click')
    await nextTick()
    const input = wrapper.get('input[type="search"]')
    await input.setValue('moto rossa')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/search')
    expect(router.currentRoute.value.query.q).toBe('moto rossa')
    expect(store.history.value.map((h) => h.q)).toEqual(['moto rossa'])
    expect(wrapper.find('input[type="search"]').exists()).toBe(false)
  })

  it('Esc richiude il campo e i suggerimenti arrivano dall\'API', async () => {
    api.suggest.mockResolvedValue({ keys: ['label:'], values: [] })
    const { wrapper } = await mountSidebar({ collapsed: false })
    await wrapper.findAll('button.navItem').find((b) => b.text() === 'Cerca')!.trigger('click')
    await nextTick()
    const input = wrapper.get('input[type="search"]')
    vi.useFakeTimers()
    await input.setValue('lab')
    vi.advanceTimersByTime(200)
    vi.useRealTimers()
    await flushPromises()
    expect(api.suggest).toHaveBeenCalledWith('lab')
    expect(wrapper.get('[role="listbox"]').text()).toContain('label:')
    await input.trigger('keydown.esc')
    expect(wrapper.find('input[type="search"]').exists()).toBe(false)
  })

  it('le ricerche recenti si fissano, si rinominano e si eliminano con conferma', async () => {
    store.history.value = [{ q: 'moto', ts: Date.now(), pinned: false }, { q: 'auto', ts: Date.now() - 1000, pinned: false }]
    const { wrapper } = await mountSidebar({ collapsed: false })
    expect(wrapper.findAll('h2.caps').map((h) => h.text())).toContain('Oggi')
    store.updateHistory('auto', { pinned: true })
    await nextTick()
    expect(wrapper.findAll('h2.caps').map((h) => h.text())).toEqual(['Libreria', 'Tipi', 'Stato', 'Fissate', 'Oggi'])
    store.updateHistory('moto', { title: 'Le mie moto' })
    await nextTick()
    expect(wrapper.text()).toContain('Le mie moto')
    store.removeHistory('moto')
    await nextTick()
    expect(wrapper.text()).not.toContain('Le mie moto')
  })

  it('non ha violazioni di accessibilità (axe)', async () => {
    store.history.value = [{ q: 'moto', ts: Date.now(), pinned: false }]
    const { wrapper } = await mountSidebar({ collapsed: false }, '/people')
    const result = await axe.run(wrapper.element as HTMLElement, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
    expect(result.violations.map((v) => `${v.id}: ${v.nodes[0]?.html}`)).toEqual([])
    const rail = track(mount(Sidebar, { props: { collapsed: true }, global: { plugins: [makeRouter('/')] }, attachTo: document.body }))
    const railResult = await axe.run(rail.element as HTMLElement, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
    expect(railResult.violations.map((v) => `${v.id}: ${v.nodes[0]?.html}`)).toEqual([])
  })
})

describe('PhotoGrid', () => {
  async function mountGrid(props: { query: string; order?: string; emptyTitle?: string; emptyText?: string } = { query: '' }, path = '/') {
    const router = makeRouter(path)
    await router.isReady()
    const wrapper = track(mount(PhotoGrid, { props, global: { plugins: [router] }, attachTo: document.body }))
    await flushPromises()
    return { router, wrapper }
  }

  it('mostra le foto con la miniatura giusta e notifica il totale', async () => {
    const { wrapper } = await mountGrid({ query: 'label:moto' })
    expect(api.photos).toHaveBeenCalledWith(expect.objectContaining({ q: 'label:moto', count: 60, offset: 0 }))
    const tiles = wrapper.findAll('button.tile')
    expect(tiles).toHaveLength(3)
    expect(wrapper.get('img').attributes('src')).toBe(`/t/${photo(1).hash}/fit_720`)
    expect(tiles[0].attributes('aria-label')).toBe('Foto 1')
    expect(wrapper.emitted('loaded')?.[0]).toEqual([3, false])
    expect(store.viewer.photos).toHaveLength(3)
  })

  it('un clic apre il dettaglio nella query e con una selezione attiva seleziona', async () => {
    const { wrapper, router } = await mountGrid()
    await wrapper.findAll('button.tile')[1].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.photo).toBe('p2')
    await wrapper.findAll('button.pick')[0].trigger('click')
    expect(store.selected.value).toEqual(['p1'])
    await wrapper.findAll('button.tile')[2].trigger('click')
    expect(store.selected.value).toEqual(['p1', 'p3'])
    expect(wrapper.findAll('button.tile')[0].attributes('aria-pressed')).toBe('true')
  })

  it('badge per video e Live Photo, cuore e lucchetto', async () => {
    api.photos.mockResolvedValue({ total: 3, semantic: false, photos: [photo(1, { type: 'video', duration: 75 }), photo(2, { type: 'live' }), photo(3, { favorite: true, private: true })] })
    const { wrapper } = await mountGrid()
    const badges = wrapper.findAll('.badge').map((b) => b.text())
    expect(badges).toEqual(['1:15', 'LIVE'])
    expect(wrapper.findAll('.marks svg').length).toBe(2)
  })

  it('stato vuoto con titolo e testo della pagina', async () => {
    api.photos.mockResolvedValue({ total: 0, semantic: false, photos: [] })
    const { wrapper } = await mountGrid({ query: '', emptyTitle: 'Niente qui', emptyText: 'Aggiungi foto' })
    expect(wrapper.text()).toContain('Niente qui')
    expect(wrapper.text()).toContain('Aggiungi foto')
  })

  it('un errore mostra l\'avviso e «Riprova» ricarica', async () => {
    api.photos.mockRejectedValueOnce(new Error('server spento'))
    const { wrapper } = await mountGrid()
    expect(wrapper.get('[role="alert"]').text()).toContain('server spento')
    api.photos.mockResolvedValue({ total: 1, semantic: false, photos: [photo(9)] })
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button.tile')).toHaveLength(1)
  })

  it('quando un job termina ricarica quanto già mostrato, senza perdere la posizione', async () => {
    await mountGrid()
    const calls = api.photos.mock.calls.length
    store.libraryVersion.value++
    await flushPromises()
    expect(api.photos.mock.calls.length).toBe(calls + 1)
    expect(api.photos).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, count: 60 }))
  })
  it('cambiare la query ricarica da zero e svuota la selezione', async () => {
    const { wrapper } = await mountGrid({ query: 'a' })
    store.toggleSelected('p1')
    await wrapper.setProps({ query: 'b' })
    await flushPromises()
    expect(api.photos).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'b', offset: 0 }))
    expect(store.selected.value).toEqual([])
  })
})

describe('AppShell', () => {
  async function mountShell(path = '/') {
    const router = makeRouter(path)
    await router.isReady()
    const wrapper = track(mount(AppShell, { global: { plugins: [router] }, attachTo: document.body }))
    await flushPromises()
    return { router, wrapper }
  }

  it('su schermo ampio la sidebar è aperta e il bookmark tab la comprime e ricorda la scelta', async () => {
    const { wrapper } = await mountShell()
    const shell = wrapper.get('.shell')
    expect(shell.attributes('data-collapsed')).toBe('false')
    await wrapper.get('.bookmarkTab').trigger('click')
    await nextTick()
    expect(shell.attributes('data-collapsed')).toBe('true')
    expect(localStorage.getItem('mosaic.sidebar')).toBe('collapsed')
    expect(wrapper.get('.bookmarkTab').attributes('aria-label')).toBe('Espandi la barra')
  })

  it('tra 960 e 1279 px parte compressa, ma la scelta dell\'utente prevale', async () => {
    mockMatchMedia((q) => q.includes('60rem'))
    const { wrapper } = await mountShell()
    expect(wrapper.get('.shell').attributes('data-collapsed')).toBe('true')
    store.setSidebarPreference('open')
    await nextTick()
    expect(wrapper.get('.shell').attributes('data-collapsed')).toBe('false')
  })

  it('sotto 960 px niente sidebar fissa: topbar con menu a scomparsa', async () => {
    mockMatchMedia(() => false)
    const { wrapper } = await mountShell()
    expect(wrapper.get('.shell').attributes('data-narrow')).toBeDefined()
    expect(wrapper.find('.sideCard').exists()).toBe(false)
    expect(wrapper.find('.topbar button[aria-label="Apri il menu"]').exists()).toBe(true)
  })

  it('il pannello filtri sostituisce la navigazione e allarga la colonna', async () => {
    const { wrapper } = await mountShell()
    store.sidebar.panel = 'filters'
    await flushPromises()
    expect(wrapper.get('.shell').attributes('data-sidebar-expanded')).toBe('true')
    expect(wrapper.text()).toContain('Cosa cerchi')
  })

  it('al primo avvio compare la procedura guidata', async () => {
    api.status.mockResolvedValue({ first_run: true, library_roots: [], photos: 0, videos: 0, analyzed: 0, people: 0, albums: 0, jobs: {},
      busy: false, assets: {}, ai_ready: false, llm_ready: false, rename_auto: true, delete_mode: 'catalog' })
    const { wrapper } = await mountShell()
    await flushPromises()
    expect(wrapper.text()).toContain('Benvenuto in Mosaic')
    expect(wrapper.text()).toContain('I modelli AI non sono ancora installati')
  })

  it('espone un solo punto di salto al contenuto e il link nel DOM', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.get('a.skip-link').attributes('href')).toBe('#main-content')
  })
})


describe('PeoplePage', () => {
  const group = { subject: 's1', name: 'Anna', markers: [{ uid: 'm1', dist: 0.9 }, { uid: 'm2', dist: 1.0 }] }

  async function mountPeople() {
    const router = makeRouter('/people')
    await router.isReady()
    const wrapper = track(mount(PeoplePage, { global: { plugins: [router] } }))
    await flushPromises()
    return wrapper
  }

  const openTab = async (wrapper: Awaited<ReturnType<typeof mountPeople>>) => {
    await wrapper.findAll('input[type="radio"]').find((r) => r.attributes('value') === 'confirm')!.trigger('change')
    await nextTick()
  }

  it('il sottotitolo e la scheda riportano quanti volti sono da confermare', async () => {
    api.suggestions.mockResolvedValue([group])
    const wrapper = await mountPeople()
    expect(wrapper.text()).toContain('2 da confermare')
    expect(wrapper.text()).toContain('Da confermare (2)')
  })

  it('senza suggerimenti la scheda spiega perché è vuota', async () => {
    const wrapper = await mountPeople()
    await openTab(wrapper)
    expect(wrapper.text()).toContain('Nessun volto da confermare')
  })

  it('raggruppa i ritagli sotto il nome della persona', async () => {
    api.suggestions.mockResolvedValue([group])
    const wrapper = await mountPeople()
    await openTab(wrapper)
    expect(wrapper.get('section h3').text()).toBe('Anna')
    expect(wrapper.findAll('section img').map((i) => i.attributes('src'))).toEqual([
      '/api/markers/m1/crop?size=160', '/api/markers/m2/crop?size=160',
    ])
  })

  it("✓ conferma il singolo volto e ricarica l'elenco", async () => {
    api.suggestions.mockResolvedValueOnce([group]).mockResolvedValue([])
    api.confirmMarker.mockResolvedValue({ subject: 's1' })
    const wrapper = await mountPeople()
    await openTab(wrapper)
    await wrapper.get('button[aria-label="È Anna"]').trigger('click')
    await flushPromises()
    expect(api.confirmMarker).toHaveBeenCalledWith('m1')
    expect(api.suggestions).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Nessun volto da confermare')
  })

  it('✗ scarta il suggerimento senza confermarlo', async () => {
    api.suggestions.mockResolvedValue([group])
    api.rejectSuggestion.mockResolvedValue(undefined)
    const wrapper = await mountPeople()
    await openTab(wrapper)
    await wrapper.findAll('button[aria-label="Non è Anna"]')[1].trigger('click')
    await flushPromises()
    expect(api.rejectSuggestion).toHaveBeenCalledWith('m2')
    expect(api.confirmMarker).not.toHaveBeenCalled()
  })

  it('«Conferma tutti» invia tutti i volti del gruppo in una sola richiesta', async () => {
    api.suggestions.mockResolvedValue([group])
    api.confirmSuggestions.mockResolvedValue({ confirmed: 2, skipped: [] })
    const wrapper = await mountPeople()
    await openTab(wrapper)
    await wrapper.get('section header button').trigger('click')
    await flushPromises()
    expect(api.confirmSuggestions).toHaveBeenCalledWith(['m1', 'm2'])
  })

  it("un errore dell'API non rompe la pagina", async () => {
    api.suggestions.mockResolvedValue([group])
    api.confirmMarker.mockRejectedValue(new Error('no'))
    const wrapper = await mountPeople()
    await openTab(wrapper)
    await wrapper.get('button[aria-label="È Anna"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('section img')).toHaveLength(2)
  })
})
