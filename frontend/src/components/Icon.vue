<script setup lang="ts">
import { computed } from 'vue'
import { resolveIcon } from '../lib/icons'

// Wrapper unico delle icone (come ui/icons.tsx di sift): 16 px, tratto 1,6, sempre decorative.
const props = withDefaults(defineProps<{ name: string; size?: number | string; strokeWidth?: number }>(), {
  size: 16,
  strokeWidth: 1.6,
})
const component = computed(() => resolveIcon(props.name))
</script>

<template>
  <component :is="component" :size="size" :stroke-width="strokeWidth" aria-hidden="true" focusable="false" />
</template>
