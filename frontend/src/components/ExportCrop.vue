<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../lib/api'
import { errorMessage, toast } from '../lib/toast'
import type { CropFormat, CropPreviewShot } from '../lib/types'
import Icon from './Icon.vue'
import Segmented from './Segmented.vue'

// «Ritaglia a formato» nell'esportazione (D-34): il formato scelto e il riquadro del ritaglio su alcune foto.
const props = defineProps<{ uids: string[] }>()
const format = defineModel<CropFormat | null>({ required: true })

const PRESETS = [
  { id: '1:1', label: '1:1', hint: 'Quadrato', w: 1, h: 1 },
  { id: '4:5', label: '4:5', hint: 'Ritratto', w: 4, h: 5 },
  { id: '3:2', label: '3:2', hint: 'Classico', w: 3, h: 2 },
  { id: '2:3', label: '2:3', hint: 'Verticale', w: 2, h: 3 },
  { id: '16:9', label: '16:9', hint: 'Schermo', w: 16, h: 9 },
  { id: '9:16', label: '9:16', hint: 'Storie', w: 9, h: 16 },
  { id: 'a4', label: 'A4', hint: 'Verticale', w: 210, h: 297 },
  { id: 'a4-wide', label: 'A4', hint: 'Orizzontale', w: 297, h: 210 },
] as const
const UNITS = [{ value: 'ratio', label: 'Rapporto' }, { value: 'px', label: 'Pixel' }] as const
const MAX_SIDE = 20000
const MAX_RATIO = 10

const choice = ref<string>('1:1')
const free = reactive<{ w: number | string; h: number | string; unit: 'ratio' | 'px' }>({ w: 1080, h: 1350, unit: 'px' })
const shots = ref<CropPreviewShot[]>([])
const loading = ref(false)

const chosen = computed<CropFormat | null>(() => {
  const preset = PRESETS.find((p) => p.id === choice.value)
  if (preset) return { w: preset.w, h: preset.h, pixels: false }
  const w = Number(free.w)
  const h = Number(free.h)
  const ok = [w, h].every((n) => Number.isInteger(n) && n >= 1 && n <= MAX_SIDE) && Math.max(w / h, h / w) <= MAX_RATIO
  return ok ? { w, h, pixels: free.unit === 'px' } : null
})
watch(chosen, (value) => { format.value = value }, { immediate: true })

const sizeNote = computed(() => (chosen.value?.pixels ? ` Ogni copia esce di ${chosen.value.w}×${chosen.value.h} pixel.` : ''))

/** Il rettangolino con le proporzioni del formato, lato lungo 1,5rem. */
function shape(w: number, h: number): Record<string, string> {
  const long = Math.max(w, h)
  return { width: `${(1.5 * w) / long}rem`, height: `${(1.5 * h) / long}rem` }
}

function frame(box: CropPreviewShot['box']): Record<string, string> {
  const [x0, y0, x1, y1] = box
  return { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }
}

let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
watch(() => [props.uids, chosen.value], () => {
  clearTimeout(timer)
  if (!chosen.value) return
  timer = setTimeout(() => void load(), 300)
}, { deep: true, immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

async function load(): Promise<void> {
  const crop = chosen.value
  if (!crop) return
  const mine = ++generation
  loading.value = true
  try {
    const { photos } = await api.exportCropPreview(props.uids, crop)
    if (mine === generation) shots.value = photos
  } catch (error) {
    if (mine === generation) toast(errorMessage(error), 'error')
  } finally {
    if (mine === generation) loading.value = false
  }
}
</script>

<template>
  <div class="crop">
    <fieldset class="formats">
      <legend class="caps">Formato</legend>
      <label v-for="p in PRESETS" :key="p.id" class="fmt" :class="{ on: choice === p.id }">
        <input v-model="choice" class="visually-hidden" type="radio" name="crop-format" :value="p.id" :data-test="`crop-${p.id}`" />
        <span class="shape" :style="shape(p.w, p.h)" aria-hidden="true" />
        <span class="fmt-label">{{ p.label }}</span>
        <span class="fmt-hint">{{ p.hint }}</span>
      </label>
      <label class="fmt" :class="{ on: choice === 'free' }">
        <input v-model="choice" class="visually-hidden" type="radio" name="crop-format" value="free" data-test="crop-free" />
        <span class="shape loose" aria-hidden="true"><Icon name="sliders" :size="14" /></span>
        <span class="fmt-label">Libero</span>
        <span class="fmt-hint">Rapporto o pixel</span>
      </label>
    </fieldset>

    <div v-if="choice === 'free'" class="free">
      <label class="field">
        <span class="label">Larghezza</span>
        <input v-model.number="free.w" class="input mono" type="number" min="1" :max="MAX_SIDE" step="1" inputmode="numeric" data-test="crop-w" />
      </label>
      <span class="by" aria-hidden="true">{{ free.unit === 'px' ? '×' : ':' }}</span>
      <label class="field">
        <span class="label">Altezza</span>
        <input v-model.number="free.h" class="input mono" type="number" min="1" :max="MAX_SIDE" step="1" inputmode="numeric" data-test="crop-h" />
      </label>
      <Segmented v-model="free.unit" :options="[...UNITS]" label="Unità del formato libero" />
    </div>
    <p v-if="!chosen" class="error-text" role="alert" data-test="crop-problem">
      Scrivi due numeri interi fra 1 e 20000; uno non può superare 10 volte l'altro.
    </p>
    <p class="hint">
      Tengo dentro i volti senza tagliarne nessuno a metà; senza volti seguo il soggetto principale. Escono solo le foto, come JPEG:
      video e RAW restano fuori.{{ sizeNote }}
    </p>

    <div v-if="chosen" class="shots" :class="{ busy: loading }" aria-live="polite">
      <p v-if="loading && !shots.length" class="hint"><Icon name="spinner" class="spin" :size="14" /> Preparo l'anteprima…</p>
      <figure v-for="s in shots" :key="s.uid" class="shot">
        <span class="frame-wrap">
          <img :src="s.image" alt="Anteprima del ritaglio" />
          <span class="frame" :style="frame(s.box)" />
        </span>
      </figure>
    </div>
  </div>
</template>

<style scoped>
.crop { display: flex; flex-direction: column; gap: var(--space-3); }
.formats { display: grid; grid-template-columns: repeat(auto-fill, minmax(5.5rem, 1fr)); gap: var(--space-2); margin: 0; padding: 0; border: 0; }
.formats legend { padding: 0 0 var(--space-1); }
.fmt {
  display: grid; justify-items: center; grid-template-rows: 1.75rem auto auto; gap: var(--space-0-5); padding: var(--space-2);
  border: 1px solid var(--border-default); border-radius: var(--radius-control); background: var(--bg-surface); cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out);
}
.fmt:hover { border-color: var(--border-strong); }
.fmt:active { transform: scale(0.97); }
.fmt.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-soft-fg); }
.fmt:has(input:focus-visible) { box-shadow: var(--focus-ring); }
.shape { align-self: center; display: grid; place-items: center; border: 1.5px solid currentColor; border-radius: 2px; opacity: 0.75; }
.shape.loose { width: 1.5rem; height: 1.5rem; border-style: dashed; }
.fmt-label { font-size: var(--text-sm); font-weight: var(--weight-semibold); font-variant-numeric: tabular-nums; }
.fmt-hint { font-size: var(--text-2xs); color: var(--fg-muted); }
.fmt.on .fmt-hint { color: inherit; }
.free { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--space-2); }
.free .field { width: 7rem; }
.by { padding-bottom: var(--space-2); color: var(--fg-muted); }
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr)); gap: var(--space-3); transition: opacity var(--dur-fast) var(--ease-out); }
.shots.busy { opacity: 0.55; }
.shot { display: grid; place-items: center; height: 9rem; margin: 0; border-radius: var(--radius-chip); background: var(--bg-sunken); }
.frame-wrap { position: relative; display: inline-block; overflow: hidden; border-radius: var(--radius-chip); }
.frame-wrap img { display: block; max-width: 100%; max-height: 9rem; }
.frame { position: absolute; border: 2px solid var(--overlay-fg); box-shadow: 0 0 0 999px var(--overlay-scrim); }
@media (prefers-reduced-motion: reduce) { .fmt { transition: none; } }
</style>
