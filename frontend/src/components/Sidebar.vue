<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../lib/api'
import { groupHistory } from '../lib/format'
import { NAV_GROUPS, isActive, type NavItem } from '../lib/nav'
import {
  currentSection, openActivity, history, pushHistory, removeHistory, searchRequest, sidebar, status, updateHistory, type HistoryItem,
} from '../lib/store'
import Dialog from './Dialog.vue'
import Dropdown from './Dropdown.vue'
import Icon from './Icon.vue'
import ProgressRing from './ProgressRing.vue'
import Tip from './Tip.vue'
import { useActivity } from '../lib/useActivity'

// Sidebar a card (struttura e misure di app/shell/Sidebar di sift-argus-v3): voce primaria, "Cerca" inline,
// navigazione a tre gruppi, ricerche recenti con menu per riga, piede con libreria e impostazioni.
const props = defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ expand: []; navigate: [] }>()

const route = useRoute()
const router = useRouter()

// --- Cerca inline ---------------------------------------------------------------------------
const searchOpen = ref(false)
const searchValue = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const suggestions = ref<string[]>([])
let suggestTimer: ReturnType<typeof setTimeout> | undefined

async function openSearch(): Promise<void> {
  if (props.collapsed) emit('expand')
  searchOpen.value = true
  await nextTick()
  searchInput.value?.focus()
}

watch(searchRequest, () => void openSearch())

function closeSearch(): void {
  searchOpen.value = false
  searchValue.value = ''
  suggestions.value = []
}

function onSearchBlur(): void {
  if (!searchValue.value.trim()) closeSearch()
}

function submitSearch(): void {
  const q = searchValue.value.trim()
  if (!q) return
  pushHistory(q)
  void router.push({ path: '/search', query: { q } })
  closeSearch()
  emit('navigate')
}

watch(searchValue, (value) => {
  if (suggestTimer) clearTimeout(suggestTimer)
  suggestTimer = setTimeout(async () => {
    const token = value.split(' ').pop() ?? ''
    if (!token) return void (suggestions.value = [])
    try {
      const data = await api.suggest(value)
      const prefix = value.slice(0, value.length - token.length)
      suggestions.value = data.keys.length
        ? data.keys.map((k) => prefix + k)
        : data.values.map((v) => `${prefix}${token.split(':')[0]}:${v}`)
    } catch {
      suggestions.value = []
    }
  }, 150)
})
onBeforeUnmount(() => suggestTimer && clearTimeout(suggestTimer))

function takeSuggestion(s: string): void {
  searchValue.value = s.endsWith(':') ? s : `${s} `
  searchInput.value?.focus()
}

// --- Ricerche recenti ------------------------------------------------------------------------------
const groups = computed(() => groupHistory(history.value.filter((h) => !searchOpen.value || !searchValue.value ||
  (h.title ?? h.q).toLowerCase().includes(searchValue.value.toLowerCase()))))
const renaming = ref<string | null>(null)
const renameValue = ref('')
const toDelete = ref<HistoryItem | null>(null)

function beginRename(item: HistoryItem): void {
  renaming.value = item.q
  renameValue.value = item.title ?? item.q
}

function commitRename(): void {
  if (renaming.value === null) return
  const title = renameValue.value.trim()
  updateHistory(renaming.value, { title: title && title !== renaming.value ? title : undefined })
  renaming.value = null
}

function confirmDelete(): void {
  if (toDelete.value) removeHistory(toDelete.value.q)
  toDelete.value = null
}

const isCurrentSearch = (item: HistoryItem): boolean => route.path === '/search' && route.query.q === item.q

function active(item: NavItem): 'page' | undefined {
  return isActive(item, route.path, currentSection.value) ? 'page' : undefined
}

const { current: work } = useActivity()
const photos = computed(() => status.value?.photos ?? 0)
</script>

<template>
  <div class="sidebar" :class="{ collapsed }">
    <!-- Barra compressa: una colonna di pulsanti quadrati -->
    <template v-if="collapsed">
      <nav class="rail" aria-label="Navigazione">
        <Tip text="Aggiungi foto" side="right">
          <RouterLink to="/library" class="btn primary icon railBtn railPrimary" aria-label="Aggiungi foto" @click="emit('navigate')">
            <Icon name="plus" />
          </RouterLink>
        </Tip>
        <Tip text="Cerca" side="right">
          <button type="button" class="btn ghost icon railBtn" aria-label="Cerca" @click="openSearch"><Icon name="search" /></button>
        </Tip>
        <template v-for="group in NAV_GROUPS" :key="group.title">
          <span class="railRule" aria-hidden="true" />
          <Tip v-for="item in group.items" :key="item.to" :text="item.label" side="right">
            <RouterLink :to="item.to" class="btn ghost icon railBtn" :aria-label="item.label" :aria-current="active(item)" @click="emit('navigate')">
              <Icon :name="item.icon" />
            </RouterLink>
          </Tip>
        </template>
      </nav>
    </template>

    <!-- Barra espansa -->
    <template v-else>
      <div class="view">
        <ul class="topmenu">
          <li>
            <RouterLink to="/library" class="btn primary newBtn" @click="emit('navigate')">
              <Icon name="plus" /> Aggiungi foto
            </RouterLink>
          </li>
          <li>
            <button v-if="!searchOpen" type="button" class="navItem" @click="openSearch"><Icon name="search" /> Cerca</button>
            <div v-else class="searchItem">
              <span class="searchIcon"><Icon name="search" /></span>
              <input
                ref="searchInput"
                v-model="searchValue"
                class="searchInput"
                type="search"
                placeholder="Cerca per contenuto o filtro…"
                aria-label="Cerca nella libreria"
                autocomplete="off"
                @keydown.enter.prevent="submitSearch"
                @keydown.esc.prevent="closeSearch"
                @blur="onSearchBlur"
              />
              <button v-if="searchValue" type="button" class="searchClear" aria-label="Svuota la ricerca" @mousedown.prevent @click="searchValue = ''">
                <Icon name="x" :size="12" />
              </button>
              <ul v-if="suggestions.length" class="suggest" role="listbox" aria-label="Suggerimenti">
                <li v-for="s in suggestions" :key="s" role="option" aria-selected="false">
                  <button type="button" class="menu-item" @mousedown.prevent @click="takeSuggestion(s)">{{ s }}</button>
                </li>
              </ul>
            </div>
          </li>
          <li>
            <button type="button" class="navItem" :aria-expanded="sidebar.panel === 'filters'" @click="sidebar.panel = 'filters'">
              <Icon name="sliders" /> Filtri
            </button>
          </li>
        </ul>

        <nav class="history" aria-label="Navigazione e ricerche recenti">
          <section v-for="group in NAV_GROUPS" :key="group.title" :aria-label="group.title">
            <h2 class="caps groupTitle">{{ group.title }}</h2>
            <ul class="list">
              <li v-for="item in group.items" :key="item.to">
                <RouterLink :to="item.to" class="navItem" :aria-current="active(item)" @click="emit('navigate')">
                  <Icon :name="item.icon" /> {{ item.label }}
                </RouterLink>
              </li>
            </ul>
          </section>

          <section v-for="group in groups" :key="group.title" :aria-label="group.title">
            <h2 class="caps groupTitle">{{ group.title }}</h2>
            <ul class="list">
              <li v-for="item in group.items" :key="item.q" class="row">
                <input
                  v-if="renaming === item.q"
                  v-model="renameValue"
                  class="renameInput"
                  aria-label="Nuovo nome della ricerca"
                  @keydown.enter.prevent="commitRename"
                  @keydown.esc.prevent="renaming = null"
                  @blur="commitRename"
                />
                <RouterLink
                  v-else
                  class="item"
                  :to="{ path: '/search', query: { q: item.q } }"
                  :aria-current="isCurrentSearch(item) ? 'page' : undefined"
                  @click="emit('navigate')"
                >
                  <span class="dot" aria-hidden="true" />
                  <span class="itemInfo">
                    <span class="title">{{ item.title ?? item.q }}</span>
                    <span v-if="item.title" class="status">{{ item.q }}</span>
                  </span>
                </RouterLink>
                <Dropdown v-if="renaming !== item.q" class="rowMenu" :label="`Azioni per «${item.title ?? item.q}»`" btn-class="rowMenuBtn">
                  <button type="button" class="menu-item" role="menuitem" @click="updateHistory(item.q, { pinned: !item.pinned })">
                    <Icon name="pin" /> {{ item.pinned ? 'Non fissare più' : 'Fissa' }}
                  </button>
                  <button type="button" class="menu-item" role="menuitem" @click="beginRename(item)"><Icon name="edit" /> Rinomina</button>
                  <div class="menu-sep" />
                  <button type="button" class="menu-item danger" role="menuitem" @click="toDelete = item"><Icon name="trash" /> Elimina</button>
                </Dropdown>
              </li>
            </ul>
          </section>
          <p v-if="!history.length" class="emptyHint">Le ricerche che avvii compaiono qui.</p>
        </nav>
      </div>
    </template>

    <div class="bottom">
      <Tip v-if="work" :text="`${work.label}: apri le attività`" side="right">
        <button type="button" class="appSettingsBtn activityBtn" data-test="activity-ring" :aria-label="`${work.label}: apri le attività`"
                @click="openActivity(); emit('navigate')">
          <ProgressRing :value="work.fraction" :size="18" label="" aria-hidden="true" />
          <span v-if="!collapsed" class="activityText truncate">{{ work.label }}</span>
          <span v-if="!collapsed && work.fraction !== null" class="activityPct mono">{{ Math.round(work.fraction * 100) }}%</span>
        </button>
      </Tip>
      <Tip v-if="collapsed" text="Libreria" side="right">
        <RouterLink to="/library" class="appSettingsBtn" aria-label="Libreria" :aria-current="route.path === '/library' ? 'page' : undefined">
          <Icon name="drive" />
        </RouterLink>
      </Tip>
      <RouterLink v-else to="/library" class="appSettingsBtn" :aria-current="route.path === '/library' ? 'page' : undefined" @click="emit('navigate')">
        <Icon name="drive" /> Libreria
      </RouterLink>
      <Tip v-if="collapsed" text="Impostazioni" side="right">
        <RouterLink to="/settings" class="appSettingsBtn" aria-label="Impostazioni" :aria-current="route.path === '/settings' ? 'page' : undefined">
          <Icon name="settings" />
        </RouterLink>
      </Tip>
      <RouterLink v-else to="/settings" class="appSettingsBtn" :aria-current="route.path === '/settings' ? 'page' : undefined" @click="emit('navigate')">
        <Icon name="settings" /> Impostazioni
      </RouterLink>
      <div class="user" :title="`${photos} foto`">
        <span class="avatar" aria-hidden="true">M</span>
        <span v-if="!collapsed" class="userText">
          <span class="userName">Mosaic</span>
          <span class="userAccount">{{ photos }} foto · in locale</span>
        </span>
      </div>
    </div>

    <Dialog :open="toDelete !== null" title="Eliminare la ricerca?" size="sm" @close="toDelete = null">
      <p>«{{ toDelete?.title ?? toDelete?.q }}» sparisce dall'elenco. Le foto non vengono toccate.</p>
      <template #footer>
        <button type="button" class="btn" @click="toDelete = null">Annulla</button>
        <button type="button" class="btn danger" @click="confirmDelete">Elimina</button>
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  height: 100%;
  min-height: 0;
  padding: var(--space-3);
  background: var(--bg-sidebar);
  border-radius: inherit;
}
.sidebar.collapsed { align-items: center; padding: var(--space-3) var(--space-2); }

.view { display: flex; flex: 1; flex-direction: column; gap: var(--space-3); min-height: 0; width: 100%; }
.topmenu { display: grid; gap: var(--space-1); flex: none; width: 100%; margin: 0; padding: 0; list-style: none; }

.newBtn { width: 100%; justify-content: flex-start; min-height: var(--nav-item-h); padding: 0 var(--space-3); gap: var(--space-2-5); font-weight: var(--weight-semibold); }

.navItem {
  display: flex;
  align-items: center;
  gap: var(--space-2-5);
  width: 100%;
  min-height: var(--nav-item-h);
  padding: 0 var(--space-2-5);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--fg-muted);
  font: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.navItem:hover { background: var(--bg-hover); color: var(--fg-default); }
.navItem[aria-current='page'] { background: var(--bg-selected); color: var(--accent-text); }

.searchItem { position: relative; display: flex; align-items: center; width: 100%; }
.searchIcon { position: absolute; top: 50%; left: var(--space-2-5); display: flex; transform: translateY(-50%); color: var(--fg-subtle-on-white); pointer-events: none; z-index: 1; }
.searchInput {
  width: 100%;
  height: var(--nav-item-h);
  padding: 0 var(--space-8) 0 calc(var(--space-2-5) + var(--space-6));
  border: var(--border-width) solid var(--border-default);
  border-radius: var(--radius-control);
  background: var(--bg-field-white);
  color-scheme: light;
  color: var(--fg-on-white);
  font-size: var(--text-sm);
  outline: none;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.searchInput:focus { border-color: var(--border-focus); box-shadow: 0 0 0 1px var(--border-focus); }
.searchInput::placeholder { color: var(--fg-subtle-on-white); }
.searchClear {
  position: absolute; top: 50%; right: var(--space-2); display: grid; place-items: center; width: var(--space-5); height: var(--space-5);
  padding: 0; border: 0; border-radius: var(--radius-pill); background: transparent; color: var(--fg-subtle-on-white);
  transform: translateY(-50%); cursor: pointer; z-index: 1;
}
.searchClear:hover { background: var(--bg-hover); color: var(--fg-on-white); }
.suggest {
  position: absolute; top: calc(100% + var(--space-1)); left: 0; right: 0; z-index: var(--z-overlay); margin: 0; padding: var(--space-1);
  list-style: none; border: 1px solid var(--border-default); border-radius: var(--radius-panel); background: var(--bg-raised);
  box-shadow: var(--shadow-3); animation: pop-in var(--dur-base) var(--ease-spring);
}

.history { flex: 1; min-height: 0; overflow-y: auto; margin: 0 calc(var(--space-1) * -1); padding: 0 var(--space-1); }
.groupTitle { padding: var(--space-4) var(--space-2-5) var(--space-1); }
.list { display: grid; gap: var(--space-0-5); margin: 0; padding: 0; list-style: none; }
.row { position: relative; }
.rowMenu { position: absolute; top: 50%; right: var(--space-1-5); transform: translateY(-50%); opacity: 0; transition: opacity var(--dur-fast) var(--ease-out); }
.row:hover .rowMenu, .row:focus-within .rowMenu { opacity: 1; }
:deep(.rowMenuBtn) {
  display: grid; place-items: center; width: var(--control-h-sm); height: var(--control-h-sm); padding: 0; border: 0;
  border-radius: var(--radius-control); background: var(--bg-sidebar); color: var(--fg-subtle); cursor: pointer;
}
:deep(.rowMenuBtn:hover), :deep(.rowMenuBtn[aria-expanded='true']) { background: var(--bg-hover); color: var(--fg-default); }
@media (hover: none) { .rowMenu { opacity: 1; } }

.renameInput {
  width: 100%; min-width: 0; height: var(--control-h-sm); padding: 0 var(--space-1-5); border: var(--border-width) solid var(--border-focus);
  border-radius: var(--radius-control); background: var(--bg-field-white); color: var(--fg-on-white); color-scheme: light; font: inherit;
  font-size: var(--text-sm); outline: none; box-shadow: 0 0 0 1px var(--border-focus);
}
.item {
  display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: var(--space-2-5);
  padding: var(--space-1-5) var(--space-2-5); border-radius: var(--radius-control); color: var(--fg-default); text-decoration: none;
  transition: background-color var(--dur-fast) var(--ease-out);
}
.row:hover .item, .row:focus-within .item { padding-right: calc(var(--control-h-sm) + var(--space-2)); }
.item:hover { background: var(--bg-hover); }
.item[aria-current='page'] { background: var(--bg-selected); }
.itemInfo { display: grid; gap: var(--space-0-5); min-width: 0; }
.title { overflow: hidden; font-size: var(--text-sm); line-height: var(--leading-tight); text-overflow: ellipsis; white-space: nowrap; }
.status { overflow: hidden; color: var(--fg-subtle); font-size: var(--text-xs); line-height: 1; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--border-strong); }
.activityBtn { width: 100%; border: 0; background: var(--bg-selected); color: var(--accent-text); font: inherit; cursor: pointer; }
.activityText { flex: 1; min-width: 0; text-align: left; }
.activityPct { font-size: var(--text-xs); font-variant-numeric: tabular-nums; }
.emptyHint { padding: var(--space-4) var(--space-2-5); color: var(--fg-muted); font-size: var(--text-sm); }

.rail { flex: 1; min-height: 0; display: flex; flex-direction: column; align-items: center; gap: var(--space-1); overflow-y: auto; overflow-x: visible; }
.railBtn { width: var(--control-h); height: var(--control-h); flex: none; }
.railBtn[aria-current='page']:not(.railPrimary) { background: var(--bg-selected); color: var(--accent-text); }
.railRule { width: var(--space-6); height: var(--border-width); margin: var(--space-1) 0; background: var(--border-default); }

.bottom { display: flex; flex-direction: column; gap: var(--space-1); flex: none; width: 100%; padding-top: var(--space-2); border-top: var(--border-width) solid var(--border-default); }
.collapsed .bottom { align-items: center; }
.appSettingsBtn {
  display: flex; align-items: center; justify-content: flex-start; gap: var(--space-2-5); width: 100%; height: var(--nav-item-h);
  padding: 0 var(--space-2); border: 0; border-radius: var(--radius-control); background: transparent; color: var(--fg-muted);
  font-size: var(--text-sm); text-decoration: none; cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.appSettingsBtn[aria-current='page'] { background: var(--bg-selected); color: var(--accent-text); }
.appSettingsBtn:hover, .appSettingsBtn:focus-visible { background: var(--bg-hover); color: var(--fg-default); }
.collapsed .appSettingsBtn { justify-content: center; width: var(--control-h); height: var(--control-h); padding: 0; }
.user { display: flex; align-items: center; gap: var(--space-2-5); width: 100%; padding: var(--space-1-5) var(--space-2); color: var(--fg-default); }
.collapsed .user { justify-content: center; width: var(--control-h); height: var(--control-h); padding: 0; }
.avatar {
  display: grid; place-items: center; width: var(--control-h-sm); height: var(--control-h-sm); flex: none; border: var(--border-width) solid var(--border-subtle);
  border-radius: var(--radius-pill); background: var(--bg-sunken); font-family: var(--font-display); font-size: var(--text-xs); font-weight: var(--weight-semibold);
}
.userText { display: grid; flex: 1; min-width: 0; line-height: var(--leading-tight); text-align: left; }
.userName { overflow: hidden; font-size: var(--text-sm); font-weight: var(--weight-medium); text-overflow: ellipsis; white-space: nowrap; }
.userAccount { overflow: hidden; color: var(--fg-subtle); font-size: var(--text-xs); text-overflow: ellipsis; white-space: nowrap; }
</style>
