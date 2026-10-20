<script setup lang="ts">
import { dismiss, toasts } from '../lib/toast'
</script>

<template>
  <div class="viewport" role="region" aria-live="polite" aria-label="Notifiche">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.tone">
      <span>{{ t.text }}</span>
      <button v-if="t.action" type="button" class="act" @click="t.action.run(); dismiss(t.id)">{{ t.action.label }}</button>
    </div>
  </div>
</template>

<style scoped>
.viewport {
  position: fixed;
  z-index: var(--z-toast);
  bottom: var(--space-4);
  left: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  transform: translateX(-50%);
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  max-width: min(32rem, calc(100vw - 2rem));
  padding: var(--space-2-5) var(--space-4);
  border-radius: var(--radius-panel);
  background: var(--bg-inverse);
  color: var(--fg-inverse);
  box-shadow: var(--shadow-4);
  font-size: var(--text-sm);
  pointer-events: auto;
  animation: toast-in var(--dur-slow) var(--ease-spring);
}
.toast.error { background: var(--deviates-bg); color: var(--deviates-fg); border: 1px solid var(--deviates-border); }
.act { border: 0; background: none; color: inherit; font: inherit; font-weight: var(--weight-medium); text-decoration: underline; cursor: pointer; }
</style>
