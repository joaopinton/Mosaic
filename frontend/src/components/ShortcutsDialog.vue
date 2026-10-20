<script setup lang="ts">
import { GO_KEYS } from '../lib/hotkeys'
import Dialog from './Dialog.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const entries = Object.entries(GO_KEYS)
</script>

<template>
  <Dialog :open="open" title="Scorciatoie da tastiera" size="sm" @close="emit('close')">
    <dl class="list">
      <div><dt><kbd>/</kbd></dt><dd>Cerca</dd></div>
      <div><dt><kbd>?</kbd></dt><dd>Mostra questo elenco</dd></div>
      <div v-for="[key, item] in entries" :key="key"><dt><kbd>g</kbd> poi <kbd>{{ key }}</kbd></dt><dd>{{ item.label }}</dd></div>
      <div><dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Foto precedente e successiva</dd></div>
      <div><dt><kbd>f</kbd></dt><dd>Preferita (nel dettaglio)</dd></div>
      <div><dt><kbd>Esc</kbd></dt><dd>Chiude finestre e menu</dd></div>
    </dl>
  </Dialog>
</template>

<style scoped>
.list { display: grid; gap: var(--space-2); margin: 0; }
.list div { display: flex; justify-content: space-between; gap: var(--space-4); font-size: var(--text-sm); }
dd { margin: 0; color: var(--fg-muted); }
kbd { padding: 1px var(--space-1-5); border: 1px solid var(--border-default); border-radius: var(--radius-chip); background: var(--bg-sunken); font-family: var(--font-mono); font-size: var(--text-xs); }
</style>
