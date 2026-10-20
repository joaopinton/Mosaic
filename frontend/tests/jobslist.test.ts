import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ cancelJobType: vi.fn(), restartJobType: vi.fn(), jobs: vi.fn() }))
vi.mock('../src/lib/api', async (original) => ({ ...(await original<typeof import('../src/lib/api')>()), api }))

import JobsList from '../src/components/JobsList.vue'
import * as store from '../src/lib/store'
import type { Job } from '../src/lib/types'

const job = (id: number, type: string, state: Job['state']): Job => ({
  id, type, state, progress: 0.3, message: '', error: '', created_at: '', finished_at: null, done: 3, total: 10,
})

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.jobs.mockResolvedValue([])
})

describe('elenco delle attività nella Libreria', () => {
  it('ferma e riavvia l\'intera fase, non un solo blocco', async () => {
    api.cancelJobType.mockResolvedValue({ cancelled: 3 })
    api.restartJobType.mockResolvedValue({ job: 9 })
    const list = [job(2, 'process', 'running'), job(3, 'process', 'queued'), job(1, 'poster', 'running')]
    api.jobs.mockResolvedValue(list)
    store.jobs.value = list
    const wrapper = mount(JobsList)
    await wrapper.get('[data-test="job-cancel"]').trigger('click')
    await flushPromises()
    expect(api.cancelJobType).toHaveBeenCalledWith('process')
    await wrapper.get('[data-test="job-restart"]').trigger('click')
    await flushPromises()
    expect(api.restartJobType).toHaveBeenCalledWith('process')
    expect(wrapper.findAll('[data-test="job-restart"]')).toHaveLength(2)  // il poster non si riavvia
    wrapper.unmount()
  })
})
