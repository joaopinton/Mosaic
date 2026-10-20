<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../lib/api'
import { clampCount, DEFAULT_PARAMS, filterQuery, MAX_COUNT, MODES, QUICK_COUNTS, SHAPES, toRouteQuery } from '../lib/curate'
import { formatCount } from '../lib/format'
import { curate, sidebar } from '../lib/store'
import type { CurateMode, CurateParams } from '../lib/types'
import Icon from './Icon.vue'
import Segmented from './Segmented.vue'

// Pannello «morph» della sidebar, come i Filtri: imposta la selezione di un album o di una cartella e la apre come una ricerca.
const emit = defineEmits<{ close: [] }>()
const router = useRouter()

const form = reactive<CurateParams>({ ...DEFAULT_PARAMS, ...(curate.draft ?? {}), seed: 0 })
const available = ref<number | null>(null)
const counting = ref(false)
const MODE_HINTS: Record<CurateMode, string> = {
  quality: 'Prima le foto venute meglio, anche se qualcuna si somiglia.',
  balanced: 'Foto belle e diverse tra loro, lungo tutta la giornata.',
  variety: 'Il più possibile diverse: momenti, luoghi e persone, anche a costo di qualche foto meno perfetta.',
}

const source = computed(() => curate.source)
const query = computed(() => (source.value ? filterQuery(source.value.uid, form) : ''))
const tooFew = computed(() => available.value !== null && available.value > 0 && available.value < form.count)

let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
watch(query, (q) => {
  clearTimeout(timer)
  if (!q) return
  counting.value = true
  timer = setTimeout(async () => {
    const mine = ++generation
    try {
      const { total } = await api.photos({ q, count: 1 })
      if (mine === generation) available.value = total
    } catch {
      if (mine === generation) available.value = null
    } finally {
      if (mine === generation) counting.value = false
    }
  }, 300)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

function setCount(value: number): void {
  form.count = clampCount(value)
}

function typedCount(event: Event): void {
  const input = event.target as HTMLInputElement
  setCount(Number(input.value))
  input.value = String(form.count) // se il valore limitato non cambia, Vue non riscriverebbe il campo
}

function close(): void {
  sidebar.panel = null
  emit('close')
}

function apply(): void {
  if (!source.value || available.value === 0) return
  void router.push({ path: `/albums/${source.value.uid}/selection`, query: toRouteQuery(form) })
  close()
}

function reset(): void {
  Object.assign(form, DEFAULT_PARAMS)
}
</script>

<template>
  <form class="panel-curate" aria-labelledby="curate-title" @submit.prevent="apply">
    <header class="head">
      <button type="button" class="btn ghost icon sm" aria-label="Torna alla navigazione" @click="close"><Icon name="chevronLeft" /></button>
      <div class="titles">
        <h2 id="curate-title">Crea selezione</h2>
        <p v-if="source" class="source truncate" :title="source.title"><Icon name="album" :size="13" /> {{ source.title }}</p>
      </div>
    </header>

    <div v-if="!source" class="fields"><p class="hint">Apri un album o una cartella e premi «Crea selezione».</p></div>
    <div v-else class="fields">
      <p class="lead">Scelgo le foto venute meglio e più diverse tra loro, lungo tutto l'evento e con più persone possibile.</p>

      <fieldset class="group">
        <legend class="caps">Quante foto</legend>
        <div class="stepper">
          <button type="button" class="btn icon" aria-label="Una foto in meno" :disabled="form.count <= 1" @click="setCount(form.count - 1)">−</button>
          <input id="c-count" class="input count" type="number" inputmode="numeric" min="1" :max="MAX_COUNT" :value="form.count"
                 aria-label="Numero di foto" @change="typedCount" />
          <button type="button" class="btn icon" aria-label="Una foto in più" :disabled="form.count >= MAX_COUNT" @click="setCount(form.count + 1)">+</button>
        </div>
        <div class="quick" role="group" aria-label="Scelte rapide">
          <button v-for="n in QUICK_COUNTS" :key="n" type="button" class="option" :class="{ on: form.count === n }"
                  :aria-pressed="form.count === n" @click="setCount(n)">{{ n }}</button>
        </div>
      </fieldset>

      <fieldset class="group">
        <legend class="caps">Formato</legend>
        <div class="shapes" role="radiogroup" aria-label="Formato delle foto">
          <label v-for="s in SHAPES" :key="s.value" class="option" :class="{ on: form.shape === s.value }">
            <input v-model="form.shape" class="visually-hidden" type="radio" name="c-shape" :value="s.value" />
            <span v-if="s.value && s.value.includes(':')" class="frame" aria-hidden="true"
                  :style="{ aspectRatio: s.value.replace(':', '/') }" />
            {{ s.label }}
          </label>
        </div>
      </fieldset>

      <fieldset class="group">
        <legend class="caps">Cosa conta di più</legend>
        <Segmented v-model="form.mode" :options="MODES" label="Cosa conta di più" />
        <p class="hint">{{ MODE_HINTS[form.mode] }}</p>
      </fieldset>

      <div class="field">
        <label for="c-text">Deve esserci <span class="subtle">(facoltativo)</span></label>
        <input id="c-text" v-model="form.text" class="input" maxlength="300" autocomplete="off"
               placeholder="per esempio: palco, torta, tramonto" />
      </div>

      <fieldset class="checks">
        <label><input v-model="form.people" type="checkbox" /> Più persone diverse possibili</label>
        <label><input v-model="form.videos" type="checkbox" /> Includi i video</label>
      </fieldset>
    </div>

    <footer v-if="source" class="foot">
      <p class="avail" role="status" aria-live="polite">
        <template v-if="counting && available === null">Conto le foto…</template>
        <template v-else-if="available === 0">Nessuna foto adatta: cambia formato o testo.</template>
        <template v-else-if="available !== null">
          {{ formatCount(available) }} {{ available === 1 ? 'foto adatta' : 'foto adatte' }}<template v-if="tooFew">: le prendo tutte</template>
        </template>
      </p>
      <div class="buttons">
        <button type="button" class="btn" @click="reset">Azzera</button>
        <button type="submit" class="btn primary" :disabled="available === 0"><Icon name="wand" /> Crea selezione</button>
      </div>
    </footer>
  </form>
</template>

<style scoped>
.panel-curate { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: var(--space-3); background: var(--bg-sidebar); border-radius: inherit; }
.head { display: flex; align-items: flex-start; gap: var(--space-2); padding-bottom: var(--space-3); }
.titles { min-width: 0; }
.head h2 { font-size: var(--text-lg); line-height: var(--control-h-sm); }
.source { display: flex; align-items: center; gap: var(--space-1); color: var(--fg-muted); font-size: var(--text-sm); }
.fields { display: flex; flex: 1; flex-direction: column; gap: var(--space-4); min-height: 0; overflow-y: auto; padding: 0 var(--space-1) var(--space-3); }
.lead { color: var(--fg-muted); font-size: var(--text-sm); line-height: var(--leading-snug); }
.group { display: flex; flex-direction: column; gap: var(--space-2); margin: 0; padding: 0; border: 0; }
.group legend { padding: 0 0 var(--space-1); }
.stepper { display: grid; grid-template-columns: auto 1fr auto; gap: var(--space-2); }
.count { text-align: center; font-family: var(--font-mono); font-size: var(--text-lg); -moz-appearance: textfield; }
.count::-webkit-inner-spin-button, .count::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.quick { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-1-5); }
.shapes { display: grid; grid-template-columns: repeat(auto-fill, minmax(5.5rem, 1fr)); gap: var(--space-1-5); }
.option {
  display: inline-flex; align-items: center; justify-content: center; gap: var(--space-1-5); min-height: var(--control-h-sm);
  padding: 0 var(--space-2); border: 1px solid var(--border-subtle); border-radius: var(--radius-control); background: var(--bg-surface);
  color: var(--fg-muted); font: inherit; font-size: var(--text-sm); cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.option:hover { border-color: var(--border-strong); color: var(--fg-default); }
.option.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-soft-fg); font-weight: var(--weight-medium); }
.option:has(input:focus-visible) { box-shadow: 0 0 0 2px var(--border-focus); }
.frame { display: inline-block; height: 0.9em; max-width: 1.4em; border: 1.5px solid currentColor; border-radius: 2px; }
.checks { display: flex; flex-direction: column; gap: var(--space-2); margin: 0; padding: 0; border: 0; font-size: var(--text-sm); }
.checks label { display: flex; align-items: center; gap: var(--space-2); }
.foot { display: flex; flex-direction: column; gap: var(--space-2); padding-top: var(--space-3); border-top: var(--border-width) solid var(--border-default); }
.avail { min-height: 1.2em; color: var(--fg-muted); font-size: var(--text-sm); }
.buttons { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
