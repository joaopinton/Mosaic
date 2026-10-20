<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import { thumbUrl } from '../lib/api'
import Icon from './Icon.vue'

// Scheda con copertina, titolo e conteggio (album, mesi, etichette). Nessun transform all'hover: cambiano solo bordo e ombra.
defineProps<{ to: RouteLocationRaw; title: string; count?: number; cover?: string | null; subtitle?: string; icon?: string }>()
</script>

<template>
  <RouterLink :to="to" class="card">
    <span class="cover">
      <img v-if="cover" :src="thumbUrl(cover, 'tile_500')" alt="" loading="lazy" />
      <Icon v-else :name="icon ?? 'images'" :size="28" />
    </span>
    <span class="meta">
      <strong class="truncate">{{ title }}</strong>
      <span class="subtle small">{{ subtitle ?? (count !== undefined ? `${count} ${count === 1 ? 'foto' : 'foto'}` : '') }}</span>
    </span>
  </RouterLink>
</template>

<style scoped>
.card {
  display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-shell);
  background: var(--bg-surface); box-shadow: var(--shadow-1); color: var(--fg-default); text-decoration: none;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-2); }
.card:focus-visible { border-color: var(--border-focus); }
.cover { display: grid; place-items: center; aspect-ratio: 4 / 3; background: var(--stage-cell); color: var(--fg-subtle); }
.cover img { width: 100%; height: 100%; object-fit: cover; }
.meta { display: grid; gap: var(--space-0-5); padding: var(--space-3); min-width: 0; }
.small { font-size: var(--text-xs); }
</style>
