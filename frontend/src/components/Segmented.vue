<script setup lang="ts" generic="T extends string">
import { LayoutGroup, Motion } from 'motion-v'
import { useId } from 'vue'
import { MORPH_SPRING } from '../lib/motion'
import Icon from './Icon.vue'

// Selettore a segmenti (radio nativi: un solo tab stop, frecce): la pillola attiva scorre da un segmento all'altro.
defineProps<{ modelValue: T; options: { value: T; label: string; icon?: string }[]; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
const group = useId()
</script>

<template>
  <LayoutGroup :id="group">
    <div class="seg" role="radiogroup" :aria-label="label">
      <label v-for="o in options" :key="o.value" class="segment" :class="{ on: o.value === modelValue }">
        <input
          class="visually-hidden"
          type="radio"
          :name="group"
          :value="o.value"
          :checked="o.value === modelValue"
          @change="emit('update:modelValue', o.value)"
        />
        <Motion v-if="o.value === modelValue" as="span" class="pill" layout-id="seg-pill" :transition="MORPH_SPRING" />
        <span class="seg-label"><Icon v-if="o.icon" :name="o.icon" />{{ o.label }}</span>
      </label>
    </div>
  </LayoutGroup>
</template>

<style scoped>
.seg {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-control);
  background: var(--bg-sunken);
}
.segment {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-height: calc(var(--control-h) - 6px);
  padding: 0 var(--space-3);
  border-radius: calc(var(--radius-control) - 2px);
  color: var(--fg-muted);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out);
}
.segment:hover { color: var(--fg-default); }
.segment.on { color: var(--fg-default); }
.segment:has(input:focus-visible) { box-shadow: var(--focus-ring); }
.pill {
  position: absolute;
  inset: 0;
  border-radius: calc(var(--radius-control) - 2px);
  background: var(--bg-surface);
  box-shadow: var(--shadow-1);
}
.seg-label { position: relative; display: inline-flex; align-items: center; gap: var(--space-1-5); }
</style>
