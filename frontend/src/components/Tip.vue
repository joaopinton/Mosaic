<script setup lang="ts">
// Tooltip: solo suggerimento (ritardo 400 ms). Il nome accessibile resta sempre sull'elemento che lo contiene.
defineProps<{ text: string; side?: 'right' | 'top' }>()
</script>

<template>
  <span class="tip" :class="side">
    <slot />
    <span class="tip-text" role="presentation">{{ text }}</span>
  </span>
</template>
