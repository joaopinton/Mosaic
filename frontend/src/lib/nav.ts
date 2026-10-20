// Voci di navigazione: un solo menu, tre gruppi (come nella sidebar di sift: voci uguali, titoli in maiuscoletto).

export interface NavItem {
  to: string
  label: string
  icon: string
  exact?: boolean
}

export interface NavGroup {
  title: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: 'Libreria',
    items: [
      { to: '/', label: 'Esplora', icon: 'images', exact: true },
      { to: '/albums', label: 'Album', icon: 'album' },
      { to: '/people', label: 'Persone', icon: 'people' },
      { to: '/labels', label: 'Etichette', icon: 'tag' },
      { to: '/calendar', label: 'Calendario', icon: 'calendar' },
      { to: '/folders', label: 'Cartelle', icon: 'folder' },
      { to: '/moments', label: 'Momenti', icon: 'sparkle' },
    ],
  },
  {
    title: 'Tipi',
    items: [
      { to: '/videos', label: 'Video', icon: 'video' },
      { to: '/live', label: 'Live Photo', icon: 'play' },
    ],
  },
  {
    title: 'Stato',
    items: [
      { to: '/favorites', label: 'Preferiti', icon: 'heart' },
      { to: '/review', label: 'Da rivedere', icon: 'review' },
      { to: '/duplicates', label: 'Duplicati', icon: 'duplicates' },
      { to: '/private', label: 'Privato', icon: 'lock' },
      { to: '/archive', label: 'Archivio', icon: 'archive' },
    ],
  },
]

/** Sezione della sidebar per il tipo di album aperto: cartelle, momenti e mesi vivono anche loro sotto /albums/. */
export const SECTION_OF: Record<string, string> = { album: '/albums', folder: '/folders', moment: '/moments', month: '/calendar' }

export function isActive(item: NavItem, path: string, section: string | null = null): boolean {
  if (section && path.startsWith('/albums/')) return item.to === section
  return item.exact ? path === item.to : path === item.to || path.startsWith(`${item.to}/`)
}
