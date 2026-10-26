import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Menu «⋯» di album, selezioni, cartelle e momenti (D-32).
const api = vi.hoisted(() => ({
  albums: vi.fn(), album: vi.fn(), patchAlbum: vi.fn(), deleteAlbum: vi.fn(), revealAlbum: vi.fn(), unlinkRoot: vi.fn(),
  exportDefaultDir: vi.fn(), jobs: vi.fn(), captionAlbum: vi.fn(), restoreAlbumTitle: vi.fn(),
}))
const allUids = vi.hoisted(() => vi.fn())
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api, allUids }))

import AlbumPage from '../src/pages/AlbumPage.vue'
import CollectionsPage from '../src/pages/CollectionsPage.vue'
import { toasts } from '../src/lib/toast'
import type { Album } from '../src/lib/types'

const album = (extra: Partial<Album>): Album => ({
  uid: 'a1', type: 'album', slug: 'a', title: 'Album', description: '', smart: false, filter: '', favorite: false, private: false,
  photo_count: 3, cover: null, source: null, path: null, root: null, renamed: false, ...extra,
})
const roma = album({ uid: 'f1', type: 'folder', slug: 'viaggi-roma', title: 'Roma', smart: true, path: 'D:\\Foto\\Viaggi\\Roma' })
const root = album({ uid: 'f2', type: 'folder', slug: 'radice-foto', title: 'Foto', smart: true, path: 'D:\\Foto', root: 'D:\\Foto' })
const hiddenOne = album({ uid: 'f3', type: 'folder', slug: 'feste', title: 'Feste', smart: true, private: true, path: 'D:\\Foto\\Feste' })

const mounted: VueWrapper[] = []

async function mountList(type: 'album' | 'folder', path = type === 'folder' ? '/folders' : '/albums') {
  const routes = [
    { path: '/albums', component: CollectionsPage, meta: { type: 'album', title: 'Album', empty: 'vuoto' } },
    { path: '/folders', component: CollectionsPage, meta: { type: 'folder', title: 'Cartelle', empty: 'vuoto' } },
    { path: '/albums/:uid', component: { template: '<div />' } },
  ]
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  const w = mount(CollectionsPage, { global: { plugins: [router] }, attachTo: document.body })
  mounted.push(w)
  await flushPromises()
  return w
}

function card(w: VueWrapper, title: string) {
  const found = w.findAll('.wrap').find((c) => c.get('strong').text() === title)
  if (!found) throw new Error(`scheda ${title} non trovata`)
  return found
}

async function openMenu(w: VueWrapper, title: string) {
  const c = card(w, title)
  await c.get('button[aria-haspopup="menu"]').trigger('click')
  return c
}

async function choose(w: VueWrapper, title: string, test: string) {
  const c = await openMenu(w, title)
  await c.get(`[data-test="${test}"]`).trigger('click')
  await flushPromises()
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  allUids.mockReset()
  api.jobs.mockResolvedValue([])
  api.exportDefaultDir.mockResolvedValue({ folder: 'C:\\Users\\me\\Desktop\\Roma' })
  toasts.value = []
})
afterEach(() => mounted.splice(0).forEach((w) => w.unmount()))

describe('CollectionsPage: menu «⋯» delle schede', () => {
  it('ogni scheda ha il suo menu e le cartelle nascoste stanno in fondo, a richiesta', async () => {
    api.albums.mockResolvedValue([roma, root, hiddenOne])
    const w = await mountList('folder')
    expect(api.albums).toHaveBeenCalledWith('folder', true)
    expect(w.findAll('.wrap').map((c) => c.get('strong').text())).toEqual(['Roma', 'Foto'])
    expect(w.findAll('button[aria-haspopup="menu"]')).toHaveLength(2)
    const toggle = w.get('[data-test="toggle-hidden"]')
    expect(toggle.text()).toContain('(1)')
    await toggle.trigger('click')
    expect(card(w, 'Feste').classes()).toContain('dimmed')
    await openMenu(w, 'Feste')
    expect(card(w, 'Feste').find('[data-test="am-show"]').exists()).toBe(true)
    expect(card(w, 'Feste').find('[data-test="am-remove"]').exists()).toBe(false)
  })

  it('nascondere una sottocartella non offre di scollegarla e si può annullare', async () => {
    api.albums.mockResolvedValue([roma, root])
    api.patchAlbum.mockImplementation(async (_uid: string, body: { private: boolean }) => ({ ...roma, private: body.private }))
    const w = await mountList('folder')
    await choose(w, 'Roma', 'am-remove')
    expect(w.text()).toContain('i file non si toccano')
    expect(w.find('[data-test="am-unlink"]').exists()).toBe(false)
    await w.get('[data-test="am-hide"]').trigger('click')
    await flushPromises()
    expect(api.patchAlbum).toHaveBeenCalledWith('f1', { private: true })
    expect(w.findAll('.wrap').map((c) => c.get('strong').text())).toEqual(['Foto'])
    const last = toasts.value.at(-1)!
    expect(last.text).toContain('non compare più in Cartelle')
    last.action!.run()
    await flushPromises()
    expect(api.patchAlbum).toHaveBeenLastCalledWith('f1', { private: false })
  })

  it('una cartella della libreria si può scollegare: il dialogo dice che i file restano', async () => {
    api.albums.mockResolvedValue([roma, root])
    api.unlinkRoot.mockResolvedValue({ job: 4 })
    const w = await mountList('folder')
    await choose(w, 'Foto', 'am-remove')
    expect(w.get('[data-test="am-unlink-hint"]').text()).toContain('D:\\Foto')
    expect(w.get('[data-test="am-unlink-hint"]').text()).toContain('restano dove sono')
    await w.get('[data-test="am-unlink"]').trigger('click')
    await flushPromises()
    expect(api.unlinkRoot).toHaveBeenCalledWith('D:\\Foto')
    expect(w.findAll('.wrap').map((c) => c.get('strong').text())).toEqual(['Roma'])
  })

  it('rimuovere un album o una selezione elimina l\'album, non le foto', async () => {
    const selection = album({ uid: 's1', title: 'Selezione del 20 ottobre', source: { uid: 'f1', title: 'Roma', type: 'folder' } })
    api.albums.mockResolvedValue([album({}), selection])
    api.deleteAlbum.mockResolvedValue(undefined)
    const w = await mountList('album')
    expect(card(w, 'Selezione del 20 ottobre').text()).toContain('da Roma')
    await choose(w, 'Selezione del 20 ottobre', 'am-remove')
    expect(w.text()).toContain('Elimino la selezione')
    expect(w.text()).toContain('Le foto restano')
    await w.get('[data-test="am-delete"]').trigger('click')
    await flushPromises()
    expect(api.deleteAlbum).toHaveBeenCalledWith('s1')
    expect(w.findAll('.wrap').map((c) => c.get('strong').text())).toEqual(['Album'])
  })

  it('rinomina una cartella solo in Mosaic, e lo dice', async () => {
    api.albums.mockResolvedValue([roma])
    api.patchAlbum.mockResolvedValue({ ...roma, title: 'Roma 2024', renamed: true })
    const w = await mountList('folder')
    await choose(w, 'Roma', 'am-rename')
    expect(w.text()).toContain('la cartella sul disco resta com\'è')
    await w.get('[data-test="am-title"]').setValue('  Roma 2024 ')
    await w.get('[data-test="am-rename-save"]').trigger('click')
    await flushPromises()
    expect(api.patchAlbum).toHaveBeenCalledWith('f1', { title: 'Roma 2024' })
    expect(card(w, 'Roma 2024')).toBeTruthy()
  })

  it('«Ripristina il nome originale» solo per cartelle e momenti rinominati', async () => {
    api.albums.mockResolvedValue([{ ...roma, title: 'Roma 2024', renamed: true }, root])
    api.restoreAlbumTitle.mockResolvedValue({ ...roma, renamed: false })
    const w = await mountList('folder')
    await openMenu(w, 'Foto')
    expect(card(w, 'Foto').find('[data-test="am-restore-title"]').exists()).toBe(false)
    await choose(w, 'Roma 2024', 'am-restore-title')
    expect(api.restoreAlbumTitle).toHaveBeenCalledWith('f1')
    expect(card(w, 'Roma')).toBeTruthy()
    expect(toasts.value.at(-1)!.text).toBe('Di nuovo «Roma»')
  })

  it('«Apri nella cartella» solo per le cartelle del disco', async () => {
    api.albums.mockResolvedValue([roma])
    api.revealAlbum.mockResolvedValue(undefined)
    const w = await mountList('folder')
    await choose(w, 'Roma', 'am-reveal')
    expect(api.revealAlbum).toHaveBeenCalledWith('f1')
    mounted.splice(0).forEach((x) => x.unmount())
    api.albums.mockResolvedValue([album({})])
    const list = await mountList('album')
    await openMenu(list, 'Album')
    expect(card(list, 'Album').find('[data-test="am-reveal"]').exists()).toBe(false)
  })

  it('«Scarica / Esporta…» apre l\'esportazione con tutte le foto dell\'album', async () => {
    api.albums.mockResolvedValue([roma])
    allUids.mockResolvedValue(['p1', 'p2', 'p3'])
    const w = await mountList('folder')
    await choose(w, 'Roma', 'am-export')
    expect(allUids).toHaveBeenCalledWith('album:f1')
    expect(w.get('[data-test="export-start"]').text()).toContain('3 foto')
  })

  it('con molte raccolte compare la ricerca, anche per percorso', async () => {
    const many = Array.from({ length: 14 }, (_, i) => album({ uid: `f${i}`, type: 'folder', title: `Cartella ${i}`, path: `D:\\Foto\\c${i}` }))
    api.albums.mockResolvedValue([...many, roma])
    const w = await mountList('folder')
    await w.get('[data-test="collections-filter"]').setValue('viaggi')
    expect(w.findAll('.wrap').map((c) => c.get('strong').text())).toEqual(['Roma'])
  })
})

describe('AlbumPage: menu «⋯» nell\'intestazione', () => {
  async function mountPage(a: Album) {
    api.album.mockResolvedValue(a)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/albums/:uid', component: AlbumPage }, { path: '/albums', component: { template: '<div />' } },
        { path: '/folders', component: { template: '<div />' } }],
    })
    await router.push(`/albums/${a.uid}`)
    await router.isReady()
    const w = mount(AlbumPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } }, attachTo: document.body })
    mounted.push(w)
    await flushPromises()
    return { w, router }
  }

  it('dopo aver eliminato l\'album torna all\'elenco', async () => {
    api.deleteAlbum.mockResolvedValue(undefined)
    const { w, router } = await mountPage(album({}))
    await w.get('button[aria-haspopup="menu"]').trigger('click')
    await w.get('[data-test="am-remove"]').trigger('click')
    await w.get('[data-test="am-delete"]').trigger('click')
    await flushPromises()
    expect(api.deleteAlbum).toHaveBeenCalledWith('a1')
    expect(router.currentRoute.value.path).toBe('/albums')
  })

  it('il pulsante Esporta e il menu usano lo stesso dialogo', async () => {
    allUids.mockResolvedValue(['p1'])
    const { w } = await mountPage(roma)
    await w.get('[data-test="album-export"]').trigger('click')
    await flushPromises()
    expect(allUids).toHaveBeenCalledWith('album:f1')
    expect(w.findAll('[data-test="export-start"]')).toHaveLength(1)
  })

  it('una cartella nascosta lo dice nel sottotitolo', async () => {
    api.patchAlbum.mockResolvedValue({ ...roma, private: true })
    const { w } = await mountPage(roma)
    await w.get('button[aria-haspopup="menu"]').trigger('click')
    await w.get('[data-test="am-remove"]').trigger('click')
    await w.get('[data-test="am-hide"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('tra le nascoste')
  })
})
