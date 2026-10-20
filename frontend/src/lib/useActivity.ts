import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { currentActivity, otherActivities, RateTracker, stageInfo, summarizePipeline, type StageSummary } from './activity'
import { jobs, status, uploads } from './store'

// Stato condiviso delle attività: le fasi, il ritmo misurato e un orologio che avanza ogni secondo mentre si lavora.
const tracker = new RateTracker()
const now = ref(Date.now())
let users = 0
let timer: ReturnType<typeof setInterval> | undefined

const pipeline = computed(() => summarizePipeline(jobs.value, now.value, status.value?.caption_auto ?? false))
const others = computed(() => otherActivities(jobs.value, now.value))

const upload = computed<StageSummary | null>(() => {
  const active = uploads.value.filter((u) => u.state === 'uploading')
  if (!active.length) return null
  const done = active.reduce((n, u) => n + u.loaded, 0)
  const total = active.reduce((n, u) => n + u.total, 0)
  return { ...stageInfo('upload'), state: 'running', done, total, fraction: total ? done / total : null,
    message: active.length === 1 ? active[0].name : `${active.length} file`, jobs: [] }
})

/** Tutto ciò che è in corso, nell'ordine in cui conviene mostrarlo. */
const all = computed<StageSummary[]>(() => [...(upload.value ? [upload.value] : []), ...pipeline.value, ...others.value])
const current = computed(() => currentActivity(all.value))
const busy = computed(() => current.value !== null)

watch(all, (stages) => {
  for (const s of stages) if (s.state === 'running' && s.total) tracker.record(s.type, s.done)
}, { immediate: true })

function remaining(stage: StageSummary | null): number | null {
  if (!stage || stage.state !== 'running') return null
  return tracker.remaining(stage.type, stage.done, stage.total, now.value)
}

/** Passo della pipeline in corso, come «3 di 6». */
const step = computed(() => {
  const index = pipeline.value.findIndex((s) => s.type === current.value?.type)
  return index >= 0 ? { index: index + 1, of: pipeline.value.length } : null
})

export function useActivity() {
  onMounted(() => {
    users++
    if (!timer) timer = setInterval(() => { if (busy.value) now.value = Date.now() }, 1000)
  })
  onBeforeUnmount(() => {
    users--
    if (users <= 0 && timer) {
      clearInterval(timer)
      timer = undefined
    }
  })
  return { pipeline, others, upload, current, busy, step, remaining, now }
}
