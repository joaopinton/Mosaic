<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'

// Menu a tendina: si chiude con Esc, con un clic fuori o scegliendo una voce.
// `placement="up"` apre il menu sopra il pulsante (per esempio nella barra in fondo allo schermo).
withDefaults(defineProps<{ label: string; icon?: string; align?: 'left' | 'right'; placement?: 'down' | 'up'; btnClass?: string }>(), {
  icon: 'more',
  align: 'right',
  placement: 'down',
  btnClass: 'btn ghost icon sm',
})

const open = ref(false)
const root = ref<HTMLElement | null>(null)

function onDocumentClick(event: MouseEvent): void {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    open.value = false
    root.value?.querySelector<HTMLElement>('button')?.focus()
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="dd">
    <button type="button" :class="btnClass" :aria-label="label" aria-haspopup="menu" :aria-expanded="open" @click="open = !open">
      <slot name="button"><Icon :name="icon" /></slot>
    </button>
    <div v-if="open" class="menu" :class="[align, placement]" role="menu" @click="open = false">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.dd { position: relative; display: inline-flex; }
.menu.down { top: calc(100% + var(--space-1)); }
.menu.up { bottom: calc(100% + var(--space-1)); transform-origin: bottom; }
.menu.right { right: 0; }
.menu.left { left: 0; }
</style>
