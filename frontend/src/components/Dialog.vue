<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { durationToken } from '../lib/motion'
import Icon from './Icon.vue'

// Dialog sull'elemento <dialog>: il focus resta intrappolato, Esc chiude, il fondo si oscura.
// Cresce dal punto cliccato (--origin-dx/--origin-dy) e si chiude con la stessa curva.
const props = withDefaults(defineProps<{
  open: boolean
  title: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  variant?: 'dialog' | 'drawer'
  side?: 'left' | 'right'
  bare?: boolean
}>(), { size: 'md', variant: 'dialog', side: 'left', bare: false })
const emit = defineEmits<{ close: [] }>()

const el = ref<HTMLDialogElement | null>(null)
const closing = ref(false)

function show(): void {
  const dialog = el.value
  if (dialog && !dialog.open) {
    closing.value = false
    dialog.showModal()
  }
}

function hide(): void {
  const dialog = el.value
  if (!dialog || !dialog.open) return
  closing.value = true
  setTimeout(() => {
    dialog.close()
    closing.value = false
  }, durationToken('--dur-base'))
}

watch(() => props.open, (value) => (value ? void nextTick(show) : hide()))
onMounted(() => props.open && show())

function onCancel(event: Event): void {
  event.preventDefault() // Esc: lo stato lo decide chi usa il dialog
  emit('close')
}

function onBackdropClick(event: MouseEvent): void {
  if (event.target === el.value) emit('close')
}
</script>

<template>
  <dialog
    ref="el"
    class="dlg"
    :class="[size, variant, { closing, right: variant === 'drawer' && side === 'right' }]"
    :aria-label="title"
    @cancel="onCancel"
    @click="onBackdropClick"
  >
    <template v-if="open || closing">
      <slot v-if="bare" />
      <template v-else>
        <header class="dlg-head">
          <h2>{{ title }}</h2>
          <button type="button" class="btn ghost icon sm" aria-label="Chiudi" @click="emit('close')">
            <Icon name="x" />
          </button>
        </header>
        <div class="dlg-body"><slot /></div>
        <footer v-if="$slots.footer" class="dlg-foot"><slot name="footer" /></footer>
      </template>
    </template>
  </dialog>
</template>
