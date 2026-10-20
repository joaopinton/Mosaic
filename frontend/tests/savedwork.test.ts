import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ albumSelections: vi.fn(), albumPosters: vi.fn(), openPoster: vi.fn(), revealPoster: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import SavedWork from '../src/components/SavedWork.vue'
import type { Album } from '../src/lib/types'

const album = (extra: Partial<Album> = {}): Album => ({
  uid: 'a1', type: 'folder', slug: 'festa', title: 'Festa', description: '', smart: true, filter: '', favorite: false,
  private: false, photo_count: 40, cover: null, ...extra,
})

describe('selezioni e poster dell\'album', () => {
  it('mostra le selezioni salvate e i poster, e apre il poster', async () => {
    api.albumSelections.mockResolvedValue([album({ uid: 's1', type: 'album', title: 'Selezione di Festa', photo_count: 24, cover: 'h' })])
    api.albumPosters.mockResolvedValue({ folder: 'D:\Festa\Mosaic', posters: [{ name: 'poster-festa-1.jpg', size: 2_400_000, created: '2026-10-20T14:49:46' }] })
    api.openPoster.mockResolvedValue(undefined)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { template: '<div />' } }] })
    const wrapper = mount(SavedWork, { props: { album: album() }, global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('Selezioni e poster')
    expect(wrapper.get('[data-test="saved-selection"]').attributes('href')).toBe('/albums/s1')
    expect(wrapper.get('[data-test="saved-poster"] img').attributes('src')).toBe('/api/albums/a1/posters/poster-festa-1.jpg')
    expect(wrapper.text()).toContain('D:\Festa\Mosaic')
    await wrapper.findAll('button').find((b) => b.text().includes('Apri'))!.trigger('click')
    expect(api.openPoster).toHaveBeenCalledWith('a1', 'poster-festa-1.jpg')
  })

  it('senza niente di salvato la sezione non compare', async () => {
    api.albumSelections.mockResolvedValue([])
    api.albumPosters.mockResolvedValue({ folder: 'x', posters: [] })
    const wrapper = mount(SavedWork, { props: { album: album() } })
    await flushPromises()
    expect(wrapper.find('.saved').exists()).toBe(false)
  })
})
