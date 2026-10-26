import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

// Esportazione in una cartella e barra della selezione (D-30).
const api = vi.hoisted(() => ({
  albums: vi.fn(), batch: vi.fn(), createAlbum: vi.fn(), exportPhotos: vi.fn(), exportDefaultDir: vi.fn(), exportPreview: vi.fn(),
  revealExport: vi.fn(), pickFolder: vi.fn(), jobs: vi.fn(), photos: vi.fn(),
}))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import { ApiError, allUids } from '../src/lib/api'
import Dropdown from '../src/components/Dropdown.vue'
import ExportDialog from '../src/components/ExportDialog.vue'
import SelectionBar from '../src/components/SelectionBar.vue'
import * as store from '../src/lib/store'
import { toasts } from '../src/lib/toast'

const mounted: { unmount: () => void }[] = []
beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.exportDefaultDir.mockResolvedValue({ folder: 'C:\\Users\\me\\Desktop\\Festa' })
  api.jobs.mockResolvedValue([])
  api.albums.mockResolvedValue([])
  toasts.value = []
})
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  store.selected.value = []
  store.jobs.value = []
})

describe('Dropdown', () => {
  it('opens above the button when placement is up', async () => {
    const w = mount(Dropdown, { props: { label: 'Menu', placement: 'up' }, slots: { default: '<button class="menu-item">A</button>' }, attachTo: document.body })
    mounted.push(w)
    await w.get('button').trigger('click')
    expect(w.get('.menu').classes()).toContain('up')
    expect(w.get('.menu').classes()).not.toContain('down')
  })
})

describe('ExportDialog', () => {
  const open = () => {
    const w = mount(ExportDialog, { props: { open: true, uids: ['p1', 'p2'], title: 'Festa', mode: 'folder' }, attachTo: document.body })
    mounted.push(w)
    return w
  }

  it('proposes the desktop folder and sends what the user chose', async () => {
    api.exportPhotos.mockResolvedValue({ job: 7, folder: 'C:\\Users\\me\\Desktop\\Festa' })
    api.exportPreview.mockResolvedValue({ photos: [] })
    const w = open()
    await flushPromises()
    expect(api.exportDefaultDir).toHaveBeenCalledWith('Festa')
    expect((w.get('[data-test="export-dest"]').element as HTMLInputElement).value).toBe('C:\\Users\\me\\Desktop\\Festa')
    await w.get('[data-test="export-improve"]').setValue(true)
    await w.get('[data-test="export-start"]').trigger('click')
    await flushPromises()
    expect(api.exportPhotos).toHaveBeenCalledWith({
      uids: ['p1', 'p2'], dest: 'C:\\Users\\me\\Desktop\\Festa', raw: false, videos: true,
      enhance: { light: true, contrast: true, vivid: true, harmonize: true, strength: 'medium' }, crop: null,
      zip: false, zip_name: 'Festa', fmt: 'original', quality: 92, max_side: null, numbered: false,
    })
    store.jobs.value = [{ id: 7, type: 'export', state: 'done', progress: 1, message: '2 foto esportate', error: '', created_at: '', finished_at: '', done: 2, total: 2 }]
    await nextTick()
    await w.get('[data-test="export-reveal"]').trigger('click')
    expect(api.revealExport).toHaveBeenCalledWith(7)
  })

  it('shows a before and after preview when improving', async () => {
    vi.useFakeTimers()
    api.exportPreview.mockResolvedValue({ photos: [{ uid: 'p1', before: 'data:image/jpeg;base64,AA', after: 'data:image/jpeg;base64,BB' }] })
    const w = open()
    await flushPromises()
    await w.get('[data-test="export-improve"]').setValue(true)
    await vi.advanceTimersByTimeAsync(400)
    await flushPromises()
    vi.useRealTimers()
    expect(api.exportPreview).toHaveBeenCalledWith(['p1', 'p2'], expect.objectContaining({ strength: 'medium' }))
    expect(w.findAll('.pair img').map((i) => i.attributes('alt'))).toEqual(['Prima', 'Dopo'])
  })

  it('falls back to typing the path outside the desktop app', async () => {
    api.pickFolder.mockRejectedValue(new ApiError(501, 'solo nell\'app desktop'))
    const w = open()
    await flushPromises()
    await w.get('[data-test="export-pick"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="export-pick"]').exists()).toBe(false)
    expect(toasts.value.at(-1)?.text).toContain('scrivi il percorso')
  })
})

describe('SelectionBar', () => {
  it('lists albums and saved selections in «Salva in…» and opens the export dialog', async () => {
    api.albums.mockResolvedValue([
      { uid: 'a1', type: 'album', slug: 'gita', title: 'Gita', description: '', smart: false, filter: '', favorite: false, private: false, photo_count: 3, cover: null, source: null },
      { uid: 'a2', type: 'album', slug: 'sel', title: 'Selezione della gita', description: '', smart: false, filter: '', favorite: false, private: false, photo_count: 2, cover: null, source: { uid: 'a1', title: 'Gita', type: 'album' } },
    ])
    store.selected.value = ['p1', 'p2']
    const w = mount(SelectionBar, { attachTo: document.body })
    mounted.push(w)
    await flushPromises()
    await w.get('[aria-label="Salva in un album o in una selezione"]').trigger('click')
    const items = w.findAll('[data-test="bar-album"]')
    expect(items.map((i) => i.text())).toEqual(['Gita', 'Selezione della gitada Gita'])
    expect(w.get('.menu').classes()).toContain('up')
    await w.get('[data-test="bar-export"]').trigger('click')
    await flushPromises()
    expect(w.findComponent(ExportDialog).props('uids')).toEqual(['p1', 'p2'])
  })
})

describe('allUids', () => {
  it('pages through a search', async () => {
    const page = (n: number, from: number) => ({ total: 600, semantic: false, photos: Array.from({ length: n }, (_, i) => ({ uid: `p${from + i}` })) })
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(page(500, 0))))
      .mockResolvedValueOnce(new Response(JSON.stringify(page(100, 500))))
    vi.stubGlobal('fetch', fetch)
    const uids = await allUids('album:a1')
    vi.unstubAllGlobals()
    expect(uids).toHaveLength(600)
    expect(fetch).toHaveBeenLastCalledWith('/api/photos?q=album%3Aa1&count=500&offset=500', expect.anything())
  })
})
