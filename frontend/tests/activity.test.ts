import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockMatchMedia } from './setup'

const api = vi.hoisted(() => ({ cancelJob: vi.fn(), cancelJobType: vi.fn(), restartJobType: vi.fn(), jobs: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import ActivityBar from '../src/components/ActivityBar.vue'
import ActivityPanel from '../src/components/ActivityPanel.vue'
import { currentActivity, formatCountOf, otherActivities, formatRemaining, RateTracker, stageInfo, summarizePipeline, summarizeStage } from '../src/lib/activity'
import * as store from '../src/lib/store'
import type { Job } from '../src/lib/types'

const T0 = Date.parse('2026-10-20T10:00:00Z')
const iso = (s: number): string => new Date(T0 + s * 1000).toISOString()
const job = (id: number, type: string, state: Job['state'], extra: Partial<Job> = {}): Job => ({
  id, type, state, progress: 0, message: '', error: '', created_at: iso(0), started_at: null, finished_at: null, done: 0, total: 0, ...extra,
})

beforeEach(() => {
  mockMatchMedia(() => false)
  store.jobs.value = []
  store.uploads.value = []
  store.sidebar.panel = null
  Object.values(api).forEach((fn) => fn.mockReset())
})
afterEach(() => { document.body.innerHTML = '' })

describe('fasi della pipeline', () => {
  it('somma i blocchi della stessa ondata: finiti, in corso e in coda', () => {
    const jobs = [
      job(4, 'process', 'queued', { created_at: iso(1), total: 60 }),
      job(3, 'process', 'running', { created_at: iso(1), done: 20, total: 60 }),
      job(2, 'process', 'done', { created_at: iso(1), finished_at: iso(30), done: 60, total: 60 }),
      job(1, 'process', 'done', { created_at: iso(-999), finished_at: iso(-500), done: 60, total: 60 }), // ondata precedente
    ]
    const s = summarizeStage(stageInfo('process'), jobs, T0 + 60_000)
    expect([s.state, s.done, s.total]).toEqual(['running', 80, 180])
    expect(s.fraction).toBeCloseTo(80 / 180)
    expect(formatCountOf(s)).toBe('80 di 180 foto')
  })

  it('senza totale la barra è indeterminata e la fase in corso viene prima di quella in coda', () => {
    const jobs = [job(2, 'analyze', 'queued'), job(1, 'index', 'running', { done: 340, message: '340 file esaminati' })]
    const stages = summarizePipeline(jobs, T0)
    expect(stages[0]).toMatchObject({ type: 'index', state: 'running', fraction: null })
    expect(stages[2].state).toBe('queued')
    expect(currentActivity(stages)?.type).toBe('index')
    expect(stages[3].state).toBe('idle')
  })

  it('le didascalie sono un passo solo se automatiche o se se ne vede il job', () => {
    const types = (stages: ReturnType<typeof summarizePipeline>): string[] => stages.map((s) => s.type)
    expect(types(summarizePipeline([], T0, false))).not.toContain('caption')
    expect(types(summarizePipeline([], T0, true))).toContain('caption')
    const asked = summarizePipeline([job(1, 'caption', 'running', { total: 4 })], T0, false)
    expect(asked.at(-1)).toMatchObject({ type: 'caption', state: 'running' })
  })

  it('una fase finita da poco risulta fatta, una vecchia torna inattiva', () => {
    const recent = summarizeStage(stageInfo('faces'), [job(1, 'faces', 'done', { finished_at: iso(0), message: '3 cluster' })], T0 + 60_000)
    expect([recent.state, recent.message]).toEqual(['done', '3 cluster'])
    const old = summarizeStage(stageInfo('faces'), [job(1, 'faces', 'done', { finished_at: iso(0) })], T0 + 3 * 3600_000)
    expect(old.state).toBe('idle')
  })
})

describe('lavori di servizio', () => {
  it('il backup dopo una modifica manuale non compare come attività', () => {
    const jobs = [job(2, 'backup', 'queued'), job(1, 'rename', 'running', { total: 10 })]
    expect(otherActivities(jobs, T0).map((s) => s.type)).toEqual(['rename'])
    expect(currentActivity(otherActivities([job(3, 'backup', 'running')], T0))).toBeNull()
  })
})

describe('elenco dei job', () => {
  it('tiene tutti i job attivi e solo gli ultimi 40 finiti', () => {
    const list = [...Array.from({ length: 100 }, (_, i) => job(1000 + i, 'process', 'queued')),
      ...Array.from({ length: 60 }, (_, i) => job(i, 'index', 'done'))]
    const kept = store.keepJobs(list)
    expect(kept.filter((j) => j.state === 'queued')).toHaveLength(100)
    expect(kept.filter((j) => j.state === 'done')).toHaveLength(40)
  })
})

describe('tempo rimanente', () => {
  it('si stima dal ritmo reale e riparte se il conteggio torna indietro', () => {
    const t = new RateTracker()
    t.record('a', 0, T0)
    expect(t.remaining('a', 0, 100, T0)).toBeNull()
    t.record('a', 10, T0 + 10_000)
    expect(t.remaining('a', 10, 100, T0 + 10_000)).toBeCloseTo(90)
    t.record('a', 2, T0 + 11_000)
    expect(t.remaining('a', 2, 100, T0 + 11_000)).toBeNull()
  })

  it('si scrive in modo umano', () => {
    expect(formatRemaining(20)).toBe('meno di un minuto')
    expect(formatRemaining(185)).toBe('circa 3 min')
    expect(formatRemaining(3 * 3600 + 20 * 60)).toBe('circa 3 h 20 min')
    expect(formatRemaining(null)).toBe('')
  })
})

describe('barra e pannello delle attività', () => {
  it('la barra mostra fase, passo, conteggio e percentuale, e apre il pannello', async () => {
    store.jobs.value = [job(1, 'analyze', 'running', { done: 45, total: 180, progress: 0.25 }), job(2, 'faces', 'queued')]
    const wrapper = mount(ActivityBar)
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Analisi AI')
    expect(text).toContain('passo 3 di 5')  // senza didascalie automatiche (predefinito) le fasi sono 5
    expect(text).toContain('45 di 180 foto')
    expect(text).toContain('25%')
    expect(text).toContain('poi altre 1 fase')
    await wrapper.get('[data-test="activity-bar"]').trigger('click')
    expect(store.sidebar.panel).toBe('activity')
    wrapper.unmount()
  })

  it('senza lavoro la barra non compare', () => {
    const wrapper = mount(ActivityBar)
    expect(wrapper.find('[data-test="activity-bar"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('i caricamenti in corso hanno la precedenza', () => {
    store.uploads.value = [{ id: 1, name: 'festa.zip', loaded: 50, total: 200, state: 'uploading', error: '' }]
    store.jobs.value = [job(1, 'index', 'running')]
    const wrapper = mount(ActivityBar)
    expect(wrapper.text()).toContain('Caricamento')
    expect(wrapper.text()).toContain('25%')
    wrapper.unmount()
  })

  it('il pannello mostra la pipeline e annulla una fase con tutti i suoi blocchi', async () => {
    api.cancelJobType.mockResolvedValue({ cancelled: 2 })
    api.jobs.mockResolvedValue([])
    store.jobs.value = [job(3, 'process', 'queued', { total: 60 }), job(2, 'process', 'running', { done: 10, total: 60 }),
      job(1, 'index', 'done', { finished_at: new Date().toISOString(), message: '120 nuove foto' })]
    const wrapper = mount(ActivityPanel, { attachTo: document.body })
    await flushPromises()
    const text = document.body.textContent ?? ''
    expect(text).toContain('Elaborazione della libreria')
    expect(text).toContain('120 nuove foto')
    expect(text).toContain('10 di 120 foto')
    const cancel = [...document.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === 'Annulla Metadati e miniature')!
    cancel.click()
    await flushPromises()
    expect(api.cancelJobType).toHaveBeenCalledWith('process')
    wrapper.unmount()
  })

  it('il pannello riavvia da capo una fase non riuscita', async () => {
    api.restartJobType.mockResolvedValue({ job: 9 })
    api.jobs.mockResolvedValue([])
    store.jobs.value = [job(1, 'analyze', 'failed', { finished_at: new Date().toISOString(), error: 'memoria esaurita' })]
    const wrapper = mount(ActivityPanel, { attachTo: document.body })
    await flushPromises()
    const restart = document.querySelector<HTMLButtonElement>('[aria-label="Riavvia Analisi AI"]')!
    restart.click()
    await flushPromises()
    expect(api.restartJobType).toHaveBeenCalledWith('analyze')
    expect(api.jobs).toHaveBeenCalled()
    wrapper.unmount()
  })
})
