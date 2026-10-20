import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ album: vi.fn(), patchAlbum: vi.fn(), captionAlbum: vi.fn(), deleteAlbum: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import AlbumPage from '../src/pages/AlbumPage.vue'
import type { Album } from '../src/lib/types'

const folderAlbum = (extra: Partial<Album> = {}): Album => ({
  uid: 'a1', type: 'folder', slug: 'radice-144', title: '144___09', description: '', smart: true, filter: 'root:"D:\\144___09"',
  favorite: false, private: false, photo_count: 202, cover: null, ...extra,
})

async function mountPage() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/albums/:uid', component: AlbumPage }] })
  await router.push('/albums/a1')
  await router.isReady()
  const wrapper = mount(AlbumPage, { global: { plugins: [router], stubs: { PhotoGrid: true, SavedWork: true } }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const mounted: { unmount: () => void }[] = []

beforeEach(() => {
  mockMatchMedia(() => false)
  Object.values(api).forEach((fn) => fn.mockReset())
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('AlbumPage: racconta la giornata', () => {
  it('mostra il racconto salvato e lo salva solo se è cambiato', async () => {
    api.album.mockResolvedValue(folderAlbum({ description: 'Gita al lago.' }))
    api.patchAlbum.mockImplementation(async (_uid: string, body: { description: string }) => folderAlbum({ description: body.description }))
    const wrapper = await mountPage()
    mounted.push(wrapper)
    const area = wrapper.get('#al-story')
    expect((area.element as HTMLTextAreaElement).value).toBe('Gita al lago.')
    const save = wrapper.findAll('button').find((b) => b.text() === 'Salva')!
    expect(save.attributes('disabled')).toBeDefined()
    await area.setValue('Gita al lago con i nonni.')
    expect(save.attributes('disabled')).toBeUndefined()
    await save.trigger('click')
    await flushPromises()
    expect(api.patchAlbum).toHaveBeenCalledWith('a1', { description: 'Gita al lago con i nonni.' })
  })

  it('rigenera le didascalie salvando prima il testo modificato', async () => {
    api.album.mockResolvedValue(folderAlbum())
    api.patchAlbum.mockResolvedValue(folderAlbum({ description: 'Giornata al circuito.' }))
    api.captionAlbum.mockResolvedValue({ job: 7, photos: 202 })
    const wrapper = await mountPage()
    mounted.push(wrapper)
    await wrapper.get('#al-story').setValue('Giornata al circuito.')
    await wrapper.findAll('button').find((b) => b.text() === 'Rigenera le didascalie')!.trigger('click')
    await flushPromises()
    expect(api.patchAlbum).toHaveBeenCalledWith('a1', { description: 'Giornata al circuito.' })
    expect(api.captionAlbum).toHaveBeenCalledWith('a1')
    expect(api.patchAlbum.mock.invocationCallOrder[0]).toBeLessThan(api.captionAlbum.mock.invocationCallOrder[0])
  })

  it('non salva se il testo non è cambiato prima di rigenerare', async () => {
    api.album.mockResolvedValue(folderAlbum({ description: 'Uguale.' }))
    api.captionAlbum.mockResolvedValue({ job: 8, photos: 3 })
    const wrapper = await mountPage()
    mounted.push(wrapper)
    await wrapper.findAll('button').find((b) => b.text() === 'Rigenera le didascalie')!.trigger('click')
    await flushPromises()
    expect(api.patchAlbum).not.toHaveBeenCalled()
    expect(api.captionAlbum).toHaveBeenCalledOnce()
  })
})
