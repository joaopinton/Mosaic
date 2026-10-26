<script setup lang="ts">
import { AnimatePresence, Motion } from 'motion-v'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { installHotkeys } from '../lib/hotkeys'
import { useMediaQuery } from '../lib/useMediaQuery'
import {
  clearSelection, gridSource, hasSelection, refreshJobs, refreshStatus, status, searchRequest, selectAllInView, setSidebarPreference, sidebar, startEvents,
} from '../lib/store'
import { errorMessage, toast } from '../lib/toast'
import { redo, undo } from '../lib/undo'
import { trackPointerOrigin } from '../lib/pointerOrigin'
import Brand from './Brand.vue'
import CuratePanel from './CuratePanel.vue'
import DropZone from './DropZone.vue'
import Dialog from './Dialog.vue'
import FiltersPanel from './FiltersPanel.vue'
import Icon from './Icon.vue'
import ActivityBar from './ActivityBar.vue'
import ActivityPanel from './ActivityPanel.vue'
import PhotoDetail from './PhotoDetail.vue'
import SelectionBar from './SelectionBar.vue'
import SetupWizard from './SetupWizard.vue'
import ShortcutsDialog from './ShortcutsDialog.vue'
import Sidebar from './Sidebar.vue'
import Tip from './Tip.vue'
import ToastViewport from './ToastViewport.vue'

// Shell a tre tavole flottanti sul canvas (sidebar, contenuto), come app/shell/AppShell di sift:
// larghezza della colonna animata, bookmark tab sul bordo, drawer sotto i 960 px, scelta dell'utente salvata.
const route = useRoute()
const router = useRouter()
const regular = useMediaQuery('(min-width: 60rem)')
const wide = useMediaQuery('(min-width: 80rem)')
const help = ref(false)

const collapsed = computed(() => (sidebar.preference ? sidebar.preference === 'collapsed' : !wide.value))
const panelOpen = computed(() => sidebar.panel !== null)
const effectiveCollapsed = computed(() => collapsed.value && !panelOpen.value)
const photoUid = computed(() => (typeof route.query.photo === 'string' ? route.query.photo : null))

function toggle(): void {
  setSidebarPreference(collapsed.value ? 'open' : 'collapsed')
}

function expand(): void {
  if (collapsed.value) setSidebarPreference('open')
}

/** Una finestra o un menu aperto: Esc deve chiudere quelli, non la selezione. */
function overlayOpen(): boolean {
  return document.querySelector('dialog[open], [role="dialog"], [role="menu"], .pswp--open') !== null
}

/** Ctrl+A: tutte le foto della pagina (non nel dettaglio di una foto, dove non ha senso). */
async function selectAllHere(): Promise<void> {
  if (route.query.photo || overlayOpen()) return
  try {
    const count = await selectAllInView()
    if (count) toast(`${count} foto selezionate`)
  } catch (error) {
    toast(errorMessage(error), 'error')
  }
}

// la selezione vale per una pagina sola: cambiando pagina (non aprendo una foto) si azzera
watch(() => route.path, () => {
  clearSelection()
  gridSource.value = null
})

/** All'avvio: se una cartella della libreria è stata spostata lo si dice subito, con la strada per ricollegarla. */
function warnMissingRoots(): void {
  const missing = status.value?.missing_roots ?? []
  if (!missing.length) return
  toast(`${missing.length === 1 ? 'Una cartella della libreria non si trova più' : `${missing.length} cartelle della libreria non si trovano più`}: spostata o rinominata?`,
        'error', { label: 'Sistema', run: () => void router.push('/library') }, 15000)
}

let cleanup: (() => void)[] = []
onMounted(() => {
  void refreshStatus().then(warnMissingRoots)
  void refreshJobs()
  cleanup = [
    startEvents(),
    trackPointerOrigin(),
    installHotkeys({
      focusSearch: () => searchRequest.value++, showHelp: () => (help.value = true), go: (path) => void router.push(path),
      selectAll: () => void selectAllHere(),
      undo: () => void undo(),
      redo: () => void redo(),
      escape: () => { if (hasSelection.value && !overlayOpen()) clearSelection() },
    }),
  ]
})
onBeforeUnmount(() => cleanup.forEach((fn) => fn()))
</script>

<template>
  <div class="shell" :data-collapsed="effectiveCollapsed" :data-sidebar-expanded="panelOpen" :data-narrow="!regular || undefined">
    <a class="skip-link" href="#main-content">Vai al contenuto</a>

    <template v-if="regular">
      <div class="leftCol">
        <div class="brandTop"><Brand :collapsed="effectiveCollapsed" /></div>
        <div class="sideCard">
          <AnimatePresence mode="wait" :initial="false">
            <Motion v-if="!panelOpen" key="nav" as="div" class="fill" :initial="{ opacity: 0, scale: 0.98 }"
                    :animate="{ opacity: 1, scale: 1 }" :exit="{ opacity: 0, scale: 0.98 }" :transition="{ duration: 0.2 }">
              <Sidebar :collapsed="effectiveCollapsed" @expand="expand" />
            </Motion>
            <Motion v-else-if="sidebar.panel === 'curate'" key="curate" as="div" class="fill" :initial="{ opacity: 0, scale: 0.98 }"
                    :animate="{ opacity: 1, scale: 1 }" :exit="{ opacity: 0, scale: 0.98 }" :transition="{ duration: 0.2 }">
              <CuratePanel @close="sidebar.panel = null" />
            </Motion>
            <Motion v-else-if="sidebar.panel === 'activity'" key="activity" as="div" class="fill" :initial="{ opacity: 0, scale: 0.98 }"
                    :animate="{ opacity: 1, scale: 1 }" :exit="{ opacity: 0, scale: 0.98 }" :transition="{ duration: 0.2 }">
              <ActivityPanel @close="sidebar.panel = null" />
            </Motion>
            <Motion v-else key="filters" as="div" class="fill" :initial="{ opacity: 0, scale: 0.98 }"
                    :animate="{ opacity: 1, scale: 1 }" :exit="{ opacity: 0, scale: 0.98 }" :transition="{ duration: 0.2 }">
              <FiltersPanel @close="sidebar.panel = null" />
            </Motion>
          </AnimatePresence>
          <Tip :text="collapsed ? 'Espandi la barra' : 'Comprimi la barra'" side="right">
            <button type="button" class="bookmarkTab" :aria-label="collapsed ? 'Espandi la barra' : 'Comprimi la barra'" @click="toggle">
              <Icon :name="collapsed ? 'chevronRight' : 'chevronLeft'" :size="14" />
            </button>
          </Tip>
        </div>
      </div>
    </template>
    <header v-else class="topbar">
      <button type="button" class="btn ghost icon" aria-label="Apri il menu" @click="sidebar.drawerOpen = true"><Icon name="menu" /></button>
      <Brand />
    </header>

    <div class="contentCard">
      <ActivityBar />
      <RouterView :key="route.path" />
    </div>

    <Dialog v-if="!regular" :open="sidebar.drawerOpen" title="Menu" variant="drawer" bare @close="sidebar.drawerOpen = false">
      <Sidebar :collapsed="false" @navigate="sidebar.drawerOpen = false" />
    </Dialog>
    <Dialog v-if="!regular" :open="sidebar.panel === 'curate'" title="Crea selezione" variant="drawer" bare @close="sidebar.panel = null">
      <CuratePanel @close="sidebar.panel = null" />
    </Dialog>
    <Dialog v-if="!regular" :open="sidebar.panel === 'activity'" title="Attività" variant="drawer" bare @close="sidebar.panel = null">
      <ActivityPanel @close="sidebar.panel = null" />
    </Dialog>
    <Dialog v-if="!regular" :open="sidebar.panel === 'filters'" title="Filtri" variant="drawer" bare @close="sidebar.panel = null">
      <FiltersPanel @close="sidebar.panel = null" />
    </Dialog>

    <PhotoDetail :uid="photoUid" />
    <DropZone />
    <SelectionBar />
    <SetupWizard />
    <ShortcutsDialog :open="help" @close="help = false" />
    <ToastViewport />
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
  gap: var(--shell-gap);
  height: 100dvh;
  padding: var(--shell-gap);
  background: var(--bg-canvas);
  transition: grid-template-columns var(--dur-slow) var(--ease-out);
}
.shell[data-collapsed='true'] { grid-template-columns: var(--sidebar-w-collapsed) minmax(0, 1fr); }
.shell[data-sidebar-expanded='true'] { grid-template-columns: min(520px, 44vw) minmax(0, 1fr); }
.shell[data-narrow] { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); gap: 0; padding: 0; transition: none; }

.leftCol { display: flex; flex-direction: column; gap: var(--space-2); min-height: 0; min-width: 0; }
.brandTop { display: flex; align-items: center; height: var(--topbar-h); flex: none; }
.sideCard {
  position: relative;
  flex: 1;
  min-height: 0;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-shell);
  background: var(--bg-sidebar);
  box-shadow: var(--shadow-shell);
  overflow: visible;
}
.fill { height: 100%; }
.bookmarkTab {
  position: absolute;
  right: -12px;
  bottom: 24px;
  z-index: 30;
  display: grid;
  place-items: center;
  width: 24px;
  height: 44px;
  padding: 0;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-control);
  background: var(--bg-surface);
  color: var(--fg-muted);
  box-shadow: var(--shadow-2);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out), transform var(--dur-instant) var(--ease-out);
}
.bookmarkTab:hover { background: var(--bg-hover); border-color: var(--border-focus); color: var(--accent-text); box-shadow: var(--shadow-3); }
.bookmarkTab:active { transform: scale(0.94); }

.contentCard {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-shell);
  background: var(--bg-surface);
  box-shadow: var(--shadow-shell);
  overflow: hidden;
}
.shell[data-narrow] .contentCard { border: 0; border-radius: 0; box-shadow: none; }
.topbar { display: flex; align-items: center; gap: var(--space-2); height: var(--topbar-h); padding: 0 var(--space-2); border-bottom: 1px solid var(--border-default); background: var(--bg-surface); }
</style>
