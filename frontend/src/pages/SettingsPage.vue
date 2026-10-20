<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '../components/Icon.vue'
import PageFrame from '../components/PageFrame.vue'
import Segmented from '../components/Segmented.vue'
import { api } from '../lib/api'
import { GO_KEYS } from '../lib/hotkeys'
import { errorMessage, toast } from '../lib/toast'
import { VIEWER_BGS, scheme, setScheme, setViewerBg, viewerBg, type Scheme, type ViewerBg } from '../lib/theme'
import type { AppConfig, CloudStatus } from '../lib/types'

const CLOUD_LINKS: Record<string, string> = { nvidia: 'https://build.nvidia.com', openrouter: 'https://openrouter.ai/keys' }

const config = ref<AppConfig | null>(null)
const cloud = ref<CloudStatus | null>(null)
const keyDraft = reactive<Record<string, string>>({})   // chiavi digitate e non ancora salvate: non tornano mai dal server
const keyShown = reactive<Record<string, boolean>>({})
const saving = ref(false)

const providers = computed(() => (config.value && cloud.value ? config.value.llm.cloud.providers.filter((p) => p in cloud.value!.providers) : []))
const hasKey = computed(() => providers.value.some((p) => cloud.value?.providers[p]?.key || keyDraft[p]?.trim()))

onMounted(async () => {
  try {
    config.value = await api.config()
    cloud.value = await api.cloudStatus()
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
})

async function removeKey(provider: string): Promise<void> {
  try {
    cloud.value = await api.putCloudKey(provider, '')
    keyDraft[provider] = ''
    toast('Chiave rimossa')
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

async function save(): Promise<void> {
  const c = config.value
  if (!c) return
  saving.value = true
  try {
    for (const provider of providers.value) {   // prima le chiavi: così il cloud acceso le trova già
      const key = keyDraft[provider]?.trim()
      if (key) {
        cloud.value = await api.putCloudKey(provider, key)
        keyDraft[provider] = ''
        keyShown[provider] = false
      }
    }
    config.value = await api.putConfig({
      rename: { auto: c.rename.auto, pattern: c.rename.pattern },
      ai: { enabled: c.ai.enabled, faces: c.ai.faces, detect_nsfw: c.ai.detect_nsfw, label_threshold: c.ai.label_threshold, max_labels: c.ai.max_labels },
      llm: {
        mode: c.llm.mode, language: c.llm.language, idle_seconds: c.llm.idle_seconds, about: c.llm.about,
        cloud: { enabled: c.llm.cloud.enabled, send_names: c.llm.cloud.send_names },
      },
      thumbs: { quality: c.thumbs.quality },
      stack_sequences: c.stack_sequences, stack_uuid: c.stack_uuid, stack_meta: c.stack_meta,
      auto_index_delay: c.auto_index_delay, workers: c.workers, delete_mode: c.delete_mode,
    })
    toast('Impostazioni salvate')
  } catch (error) {
    toast(errorMessage(error), 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PageFrame title="Impostazioni" width="reading">
    <template #actions>
      <button type="button" class="btn primary" :disabled="saving || !config" @click="save">Salva</button>
    </template>

    <section class="panel panel-pad block" aria-labelledby="s-look">
      <h2 id="s-look">Aspetto</h2>
      <div class="field"><span class="label" id="theme-label">Tema</span>
        <Segmented :model-value="scheme" label="Tema" :options="[{ value: 'light', label: 'Chiaro', icon: 'sun' }, { value: 'dark', label: 'Scuro', icon: 'moon' }, { value: 'system', label: 'Sistema', icon: 'monitor' }]"
                   @update:model-value="(v: string) => setScheme(v as Scheme)" /></div>
      <div class="field"><span class="label" id="viewer-label">Sfondo del visore a tutto schermo</span>
        <Segmented :model-value="viewerBg" label="Sfondo del visore" :options="[...VIEWER_BGS]"
                   @update:model-value="(v: string) => setViewerBg(v as ViewerBg)" /></div>
    </section>

    <template v-if="config">
      <section class="panel panel-pad block" aria-labelledby="s-ai">
        <h2 id="s-ai">Analisi AI</h2>
        <label class="option"><input v-model="config.ai.enabled" type="checkbox" /><span><strong>Analizza le foto</strong><span class="hint">Embedding per la ricerca, etichette e foto simili.</span></span></label>
        <label class="option"><input v-model="config.ai.faces" type="checkbox" /><span><strong>Riconosci i volti</strong><span class="hint">Raggruppa le persone; i nomi li assegni tu.</span></span></label>
        <label class="option"><input v-model="config.ai.detect_nsfw" type="checkbox" /><span><strong>Nascondi i contenuti sensibili</strong><span class="hint">Le foto riconosciute come tali diventano private.</span></span></label>
        <div class="field"><label for="thr">Sicurezza minima delle etichette: {{ Math.round(config.ai.label_threshold * 100) }}%</label>
          <input id="thr" v-model.number="config.ai.label_threshold" type="range" min="0.1" max="0.9" step="0.05" /></div>
        <div class="field"><label for="maxl">Etichette per foto (al massimo)</label>
          <input id="maxl" v-model.number="config.ai.max_labels" class="input" type="number" min="1" max="12" /></div>
      </section>

      <section class="panel panel-pad block" aria-labelledby="s-llm">
        <h2 id="s-llm">Descrizioni con l'AI</h2>
        <div class="field"><label for="llm-mode">Quando</label>
          <select id="llm-mode" v-model="config.llm.mode" class="select" data-test="llm-mode">
            <option value="on-demand">Solo quando le chiedo, dalla Libreria</option>
            <option value="newly-indexed">In automatico per le foto nuove, dopo gli album</option>
            <option value="never">Mai</option></select>
          <span class="hint">Di base non fanno parte dell'elaborazione delle foto: sono il passo più lento (decine di secondi a foto sul PC).</span></div>
        <label class="option"><input v-model="config.llm.caption" type="checkbox" data-test="llm-caption" />
          <span><strong>Scrivi anche la didascalia</strong>
            <span class="hint">Una frase che descrive la foto, oltre a titolo e parole chiave. È la parte più lenta: circa metà del tempo di ogni foto.</span></span></label>
        <div class="field"><label for="llm-lang">Lingua</label>
          <select id="llm-lang" v-model="config.llm.language" class="select"><option value="it">Italiano</option><option value="en">English</option></select></div>
        <div class="field"><label for="llm-about">Chi sei e cosa fotografi</label>
          <textarea id="llm-about" v-model="config.llm.about" class="input" rows="3" maxlength="2000"
                    placeholder="Per esempio: foto di famiglia a Padova e di moto d'epoca; Stella e Camilla sono le mie figlie." />
          <span class="hint">Entra nel contesto di ogni didascalia, insieme alla giornata scritta nell'album e alla data dello scatto.</span></div>
        <div class="field"><label for="llm-idle">Spegni il modello dopo (secondi di inattività)</label>
          <input id="llm-idle" v-model.number="config.llm.idle_seconds" class="input" type="number" min="30" max="3600" /></div>

        <div class="cloud" role="group" aria-labelledby="cloud-title">
          <h3 id="cloud-title">Più veloce con il cloud <span class="tag">facoltativo</span></h3>
          <label class="option"><input v-model="config.llm.cloud.enabled" type="checkbox" data-test="cloud-enabled" />
            <span><strong>Usa un servizio cloud gratuito per le didascalie</strong>
              <span class="hint">Molto più rapido del PC. Se il servizio è al limite o non risponde, Mosaic passa all'altro e poi al modello locale, che resta sempre installato.</span></span></label>

          <div v-for="provider in providers" :key="provider" class="field" :data-test="`key-${provider}`">
            <label :for="`key-${provider}`">Chiave {{ cloud!.providers[provider].label }}</label>
            <div class="keyrow">
              <input :id="`key-${provider}`" v-model="keyDraft[provider]" class="input mono" :type="keyShown[provider] ? 'text' : 'password'"
                     autocomplete="off" spellcheck="false" maxlength="512"
                     :placeholder="cloud!.providers[provider].key === 'file' ? 'Chiave salvata: scrivine una nuova per sostituirla' : cloud!.providers[provider].key === 'env' ? 'Presa dalla variabile d\'ambiente' : 'Incolla qui la chiave'" />
              <button type="button" class="btn" :aria-pressed="!!keyShown[provider]" @click="keyShown[provider] = !keyShown[provider]">{{ keyShown[provider] ? 'Nascondi' : 'Mostra' }}</button>
              <button v-if="cloud!.providers[provider].key === 'file'" type="button" class="btn ghost" @click="removeKey(provider)">Rimuovi</button>
            </div>
            <span class="hint">
              <template v-if="cloud!.providers[provider].key === 'file'">Salvata ✓ nel file <code>data/secrets.yaml</code>. </template>
              <template v-else-if="cloud!.providers[provider].key === 'env'">Trovata nelle variabili d'ambiente ✓. </template>
              Chiave gratuita su <a :href="CLOUD_LINKS[provider]" target="_blank" rel="noopener noreferrer">{{ CLOUD_LINKS[provider].replace('https://', '') }}</a>.
              Modello: <code>{{ cloud!.providers[provider].model }}</code>.
            </span>
          </div>

          <p v-if="config.llm.cloud.enabled && !hasKey" class="hint warn" role="status" data-test="cloud-nokey">
            Aggiungi almeno una chiave: finché manca, le didascalie restano sul modello locale.</p>

          <label class="option"><input v-model="config.llm.cloud.send_names" type="checkbox" />
            <span><strong>Invia anche i nomi delle persone</strong><span class="hint">I nomi che hai confermato nella foto entrano nel testo inviato.</span></span></label>
          <p class="hint"><Icon name="info" :size="14" /> Con il cloud acceso le foto escono dal tuo PC: Mosaic invia a NVIDIA o OpenRouter la foto ridotta (lato lungo {{ config.llm.image_size }} px) e il testo «Chi sei», la giornata dell'album e la data. I piani gratuiti possono conservare quello che ricevono. Volti, ricerca e il resto restano sempre sul tuo PC.</p>
        </div>
      </section>

      <section class="panel panel-pad block" aria-labelledby="s-rename">
        <h2 id="s-rename">Rinomina e raggruppamento</h2>
        <label class="option"><input v-model="config.rename.auto" type="checkbox" /><span><strong>Rinomina automatica dei file</strong><span class="hint">Dopo l'analisi. Annullabile dalla Libreria.</span></span></label>
        <div class="field"><label for="rn-pattern">Schema dei nomi</label><input id="rn-pattern" v-model="config.rename.pattern" class="input mono" autocomplete="off" /></div>
        <label class="option"><input v-model="config.stack_uuid" type="checkbox" /><span><strong>Unisci le foto con lo stesso identificativo XMP</strong></span></label>
        <label class="option"><input v-model="config.stack_sequences" type="checkbox" /><span><strong>Raggruppa i file numerati</strong><span class="hint">«foto (2).jpg» e «foto copy 2.jpg» entrano nello scatto di «foto.jpg».</span></span></label>
        <label class="option"><input v-model="config.stack_meta" type="checkbox" /><span><strong>Unisci gli scatti con stessa data, posizione e fotocamera</strong><span class="hint">Sconsigliato con le raffiche.</span></span></label>
      </section>

      <section class="panel panel-pad block" aria-labelledby="s-delete" data-test="delete-section">
        <h2 id="s-delete">Sincronizzazione con il computer</h2>
        <p class="hint">Quanto Mosaic e le cartelle del tuo PC vanno di pari passo. Mosaic non cancella mai un file in modo definitivo e chiede sempre conferma prima di eliminare.</p>
        <div class="modes" role="radiogroup" aria-label="Sincronizzazione con il computer">
          <label class="mode" :class="{ on: config.delete_mode === 'catalog' }">
            <input v-model="config.delete_mode" class="visually-hidden" type="radio" name="sync-mode" value="catalog" />
            <span class="mode-head"><Icon name="eye" /> <strong>Solo interfaccia</strong></span>
            <span class="mode-text">Mosaic è un modo più semplice per guardare e ordinare le foto: i file sul disco non si toccano.</span>
          </label>
          <label class="mode" :class="{ on: config.delete_mode === 'trash' }">
            <input v-model="config.delete_mode" class="visually-hidden" type="radio" name="sync-mode" value="trash" />
            <span class="mode-head"><Icon name="refresh" /> <strong>Sincronizzata con il computer</strong></span>
            <span class="mode-text">Mosaic e le cartelle sono la stessa cosa: quello che elimini da una parte sparisce anche dall'altra.</span>
          </label>
        </div>
        <ul v-if="config.delete_mode === 'catalog'" class="facts" data-test="delete-hint-catalog">
          <li><strong>Elimina</strong> toglie la foto da Mosaic (ricerca, album, persone): il file resta dov'è sul disco e non viene più indicizzato. Dalla Libreria puoi rimetterla.</li>
          <li>Un file cancellato dal computer resta in Mosaic come «mancante» finché non fai la pulizia dalla Libreria.</li>
          <li v-if="config.rename.auto" class="warn">La <strong>rinomina automatica</strong> è accesa e cambia i nomi dei file sul disco: per usare Mosaic solo come interfaccia spegnila in «Rinomina e raggruppamento».</li>
        </ul>
        <ul v-else class="facts" data-test="delete-hint-trash">
          <li><strong>Elimina</strong> sposta tutti i file dello scatto (JPEG, RAW, XMP, video) nel Cestino di Windows, da cui li puoi ripristinare. Se l'unità non ha un Cestino (rete, chiavetta) il file non si tocca e la foto resta.</li>
          <li>I file che cancelli dal computer spariscono anche da Mosaic al controllo successivo delle cartelle. Una cartella scollegata o un disco staccato non contano: le foto restano.</li>
          <li><label class="inline"><input type="checkbox" :checked="config.auto_index_delay > 0" data-test="sync-watch"
                 @change="config.auto_index_delay = ($event.target as HTMLInputElement).checked ? 30 : 0" />
            Accorgiti da sola dei cambiamenti nelle cartelle (dopo 30 secondi di quiete)</label></li>
        </ul>
      </section>

      <section class="panel panel-pad block" aria-labelledby="s-perf">
        <h2 id="s-perf">Prestazioni</h2>
        <div class="field"><label for="workers">Elaborazioni in parallelo (0 = automatico)</label>
          <input id="workers" v-model.number="config.workers" class="input" type="number" min="0" max="16" /></div>
        <div class="field"><label for="auto-idx">Controlla le cartelle da sola dopo (secondi di quiete, 0 = mai)</label>
          <input id="auto-idx" v-model.number="config.auto_index_delay" class="input" type="number" min="0" max="86400" /></div>
        <div class="field"><label for="q">Qualità delle miniature JPEG</label>
          <input id="q" v-model.number="config.thumbs.quality" class="input" type="number" min="40" max="100" /></div>
      </section>
    </template>

    <section class="panel panel-pad block" aria-labelledby="s-keys">
      <h2 id="s-keys">Scorciatoie</h2>
      <dl class="keys">
        <div><dt><kbd>/</kbd></dt><dd>Cerca</dd></div>
        <div v-for="(item, key) in GO_KEYS" :key="key"><dt><kbd>g</kbd> poi <kbd>{{ key }}</kbd></dt><dd>{{ item.label }}</dd></div>
        <div><dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Foto precedente / successiva</dd></div>
        <div><dt><kbd>?</kbd></dt><dd>Mostra le scorciatoie</dd></div>
      </dl>
      <p class="hint"><Icon name="info" :size="14" /> I dati restano sul tuo PC: la configurazione è in <code>config.yaml</code>.</p>
    </section>
  </PageFrame>
</template>

<style scoped>
.block { display: flex; flex-direction: column; gap: var(--space-3); margin-bottom: var(--space-4); }
h2 { font-size: var(--text-lg); }
h3 { display: flex; align-items: center; gap: var(--space-2); font-size: var(--text-base); }
.cloud { display: flex; flex-direction: column; gap: var(--space-3); margin-top: var(--space-2); padding-top: var(--space-4); border-top: 1px solid var(--border-default); }
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: var(--space-3); }
.mode {
  display: flex; flex-direction: column; gap: var(--space-1-5); padding: var(--space-3) var(--space-4); border: 1px solid var(--border-default);
  border-radius: var(--radius-panel); background: var(--bg-surface); cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.mode:hover { border-color: var(--border-strong); }
.mode.on { border-color: var(--accent); background: var(--bg-selected); box-shadow: 0 0 0 1px var(--accent); }
.mode:has(input:focus-visible) { box-shadow: 0 0 0 2px var(--border-focus); }
.mode-head { display: flex; align-items: center; gap: var(--space-2); }
.mode.on .mode-head { color: var(--accent-text); }
.mode-text { color: var(--fg-muted); font-size: var(--text-sm); line-height: var(--leading-snug); }
.facts { display: flex; flex-direction: column; gap: var(--space-2); margin: 0; padding-left: var(--space-5); color: var(--fg-muted); font-size: var(--text-sm); }
.facts .warn { color: var(--to-verify-fg); }
.inline { display: inline-flex; align-items: center; gap: var(--space-2); color: var(--fg-default); }
.tag { padding: 1px var(--space-1-5); border: 1px solid var(--border-default); border-radius: var(--radius-chip); color: var(--fg-muted); font-size: var(--text-xs); font-weight: var(--weight-regular); }
.keyrow { display: flex; gap: var(--space-2); }
.keyrow .input { flex: 1; min-width: 0; }
.warn { color: var(--deviates-fg); }
.option { display: flex; gap: var(--space-3); align-items: flex-start; font-size: var(--text-sm); cursor: pointer; }
.option span { display: grid; gap: var(--space-0-5); }
.keys { display: grid; gap: var(--space-1-5); margin: 0; font-size: var(--text-sm); }
.keys div { display: flex; justify-content: space-between; gap: var(--space-4); }
dd { margin: 0; color: var(--fg-muted); }
kbd { padding: 1px var(--space-1-5); border: 1px solid var(--border-default); border-radius: var(--radius-chip); background: var(--bg-sunken); font-family: var(--font-mono); font-size: var(--text-xs); }
code { font-family: var(--font-mono); font-size: var(--text-xs); }
</style>
