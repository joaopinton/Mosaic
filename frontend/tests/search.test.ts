import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ labels: vi.fn(), subjects: vi.fn(), albums: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import FiltersPanel from '../src/components/FiltersPanel.vue'
import { addFilter, buildQuery, emptyForm, formFromQuery } from '../src/lib/query'
import { sidebar } from '../src/lib/store'
import SearchPage from '../src/pages/SearchPage.vue'

describe('pannello Filtri ↔ query', () => {
  it('fa andata e ritorno con valori multipli', () => {
    const form = {
      ...emptyForm(), text: 'moto rossa', type: 'video', year: '2022-2024', camera: 'nikon d850', orientation: 'landscape' as const,
      people: ['anna', 'marco'], labels: ['moto', 'palco'], albums: ['gita', 'mare'], colors: ['rosso', 'blu'], favorite: true,
    }
    const q = buildQuery(form)
    expect(q).toBe('moto rossa type:video year:2022-2024 label:moto label:palco person:anna person:marco album:gita|mare '
      + 'camera:"nikon d850" color:rosso|blu landscape:true favorite:true')
    expect(formFromQuery(q)).toEqual(form)
  })

  it('conserva i filtri che non sa mostrare', () => {
    const form = formFromQuery('person:anna|marco -color:nero iso:800+ album:a album:b persona:luca preferiti:true')
    expect(form.people).toEqual(['luca'])
    expect(form.albums).toEqual(['a'])
    expect(form.favorite).toBe(true)
    expect(form.extra).toEqual(['person:anna|marco', '-color:nero', 'iso:800+', 'album:b'])
    expect(buildQuery(form)).toBe('person:luca album:a favorite:true person:anna|marco -color:nero iso:800+ album:b')
    expect(buildQuery(emptyForm())).toBe('')
  })

  it('aggiunge un filtro senza sostituire gli altri', () => {
    expect(addFilter('moto person:anna', 'person', 'marco')).toBe('moto person:anna person:marco')
    expect(addFilter('person:anna', 'person', 'anna')).toBe('person:anna')
    expect(addFilter('', 'person', 'Anna Rossi')).toBe('person:"Anna Rossi"')
    expect(addFilter('moto', 'label', '  ')).toBe('moto')
  })
})

async function makeRouter(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/search', component: SearchPage }, { path: '/', component: { template: '<div />' } },
  ] })
  await router.push(path)
  await router.isReady()
  return router
}

const mounted: { unmount: () => void }[] = []

beforeEach(() => {
  sidebar.panel = null
  api.labels.mockResolvedValue([{ slug: 'moto', name: 'Moto', count: 3 }])
  api.subjects.mockResolvedValue([{ uid: 's1', slug: 'anna', name: 'Anna' }, { uid: 's2', slug: 'marco', name: 'Marco' }])
  api.albums.mockResolvedValue([])
})

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('SearchPage: chip dei filtri', () => {
  async function mountPage(path: string) {
    const router = await makeRouter(path)
    const wrapper = mount(SearchPage, { global: { plugins: [router], stubs: { PhotoGrid: true } }, attachTo: document.body })
    mounted.push(wrapper)
    await flushPromises()
    return { wrapper, router }
  }

  it("togliendo l'ultimo chip torna a /search senza query", async () => {
    const { wrapper, router } = await mountPage('/search?q=person%3Aanna')
    await wrapper.get('[aria-label="Togli il filtro person: anna"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/search')
  })

  it('toglie un chip alla volta e «Togli tutti» lascia il testo', async () => {
    const { wrapper, router } = await mountPage('/search?q=' + encodeURIComponent('moto person:anna person:marco'))
    await wrapper.get('[aria-label="Togli il filtro person: anna"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBe('moto person:marco')
    await router.push('/search?q=' + encodeURIComponent('moto person:anna color:blu'))
    await flushPromises()
    await wrapper.get('[data-test="clear-filters"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBe('moto')
  })

  it('«+ Filtro» apre il pannello Filtri', async () => {
    const { wrapper } = await mountPage('/search?q=moto')
    await wrapper.get('[data-test="add-filter"]').trigger('click')
    expect(sidebar.panel).toBe('filters')
  })
})

describe('FiltersPanel', () => {
  it('si precompila dalla ricerca corrente e aggiunge o toglie persone', async () => {
    const router = await makeRouter('/search?q=' + encodeURIComponent('moto person:anna color:rosso|blu'))
    const wrapper = mount(FiltersPanel, { global: { plugins: [router] }, attachTo: document.body })
    mounted.push(wrapper)
    await flushPromises()
    expect((wrapper.get('#f-text').element as HTMLInputElement).value).toBe('moto')
    expect(wrapper.get('[aria-label="Persone scelte"]').text()).toContain('Anna')
    expect(wrapper.get('[aria-label="Colori scelte"]').text()).toContain('rosso')

    await wrapper.get('#f-person').setValue('marco')
    await wrapper.get('[aria-label="Togli rosso"]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBe('moto person:anna person:marco color:blu')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
