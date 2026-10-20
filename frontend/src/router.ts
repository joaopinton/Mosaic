import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

// Pagine che sono solo una query sulla libreria: stesso componente, titolo e filtro nel meta della rotta.
type BrowseMeta = { title: string; query: string; order?: string; emptyTitle?: string; emptyText?: string }
const browse = (path: string, meta: BrowseMeta): RouteRecordRaw => ({ path, component: () => import('./pages/BrowsePage.vue'), meta })
const collection = (path: string, type: string, title: string, empty: string): RouteRecordRaw => ({
  path, component: () => import('./pages/CollectionsPage.vue'), meta: { type, title, empty },
})

export const routes: RouteRecordRaw[] = [
  browse('/', { title: 'Esplora', query: '', emptyTitle: 'La libreria è vuota', emptyText: 'Aggiungi una cartella di foto dalla Libreria: Mosaic le indicizza e le analizza da solo.' }),
  browse('/favorites', { title: 'Preferiti', query: 'favorite:true', emptyTitle: 'Nessun preferito', emptyText: 'Premi il cuore su una foto per ritrovarla qui.' }),
  browse('/videos', { title: 'Video', query: 'type:video', emptyTitle: 'Nessun video', emptyText: 'I video della libreria compaiono qui.' }),
  browse('/live', { title: 'Live Photo', query: 'live:true', emptyTitle: 'Nessuna Live Photo', emptyText: 'Una foto con un breve video nello stesso scatto diventa una Live Photo.' }),
  browse('/review', { title: 'Da rivedere', query: 'review:true', emptyTitle: 'Niente da rivedere', emptyText: 'Qui finiscono le foto con pochi dati affidabili (senza data, posizione o fotocamera).' }),
  browse('/private', { title: 'Privato', query: 'private:true', emptyTitle: 'Nessuna foto privata', emptyText: 'Le foto private non compaiono in Esplora né nelle ricerche.' }),
  browse('/archive', { title: 'Archivio', query: 'archived:true', emptyTitle: 'Archivio vuoto', emptyText: 'Le foto archiviate si possono ripristinare in qualsiasi momento. I file originali non vengono mai cancellati.' }),
  browse('/random', { title: 'Casuali', query: '', order: 'random', emptyTitle: 'Nessuna foto', emptyText: 'Aggiungi qualche foto alla libreria.' }),

  { path: '/search', component: () => import('./pages/SearchPage.vue') },
  collection('/albums', 'album', 'Album', 'Crea un album dalla barra delle azioni: seleziona alcune foto e scegli «Album».'),
  collection('/folders', 'folder', 'Cartelle', 'Le cartelle della libreria compaiono qui dopo l\'indicizzazione.'),
  collection('/moments', 'moment', 'Momenti', 'I momenti nascono da etichette e persone frequenti, dopo l\'analisi AI.'),
  { path: '/albums/:uid', component: () => import('./pages/AlbumPage.vue') },
  { path: '/albums/:uid/selection', component: () => import('./pages/SelectionPage.vue') },
  { path: '/calendar', component: () => import('./pages/CalendarPage.vue') },
  { path: '/people', component: () => import('./pages/PeoplePage.vue') },
  { path: '/people/:uid', component: () => import('./pages/PersonPage.vue') },
  { path: '/labels', component: () => import('./pages/LabelsPage.vue') },
  { path: '/duplicates', component: () => import('./pages/DuplicatesPage.vue') },
  { path: '/library', component: () => import('./pages/LibraryPage.vue') },
  { path: '/settings', component: () => import('./pages/SettingsPage.vue') },
  { path: '/:rest(.*)*', component: () => import('./pages/NotFoundPage.vue') },
]

export const router = createRouter({ history: createWebHistory(), routes })
