<script setup lang="ts">
defineProps<{ collapsed?: boolean }>()
</script>

<template>
  <RouterLink to="/" class="brand" :class="{ collapsed }" aria-label="Mosaic: vai a Esplora">
    <svg class="logo" viewBox="0 0 256 256" aria-hidden="true" focusable="false">
      <rect class="rim" width="256" height="256" rx="60" />
      <rect class="plate" x="14" y="14" width="228" height="228" rx="46" />
      <circle class="dot red" cx="86" cy="86" r="38" />
      <circle class="dot orange" cx="170" cy="86" r="38" />
      <circle class="dot cyan" cx="86" cy="170" r="38" />
      <circle class="dot yellow" cx="170" cy="170" r="38" />
    </svg>
    <span v-if="!collapsed" class="text">
      <span class="name">MOSAIC</span>
      <span class="by">foto in locale</span>
    </span>
  </RouterLink>
</template>

<style scoped>
.brand { display: flex; align-items: center; gap: var(--space-2-5); padding: 0 var(--space-3); color: var(--fg-default); text-decoration: none; }
.brand.collapsed { justify-content: center; padding: 0; }
.logo { width: 32px; height: 32px; flex: none; }
/* Il logo non cambia con il tema: stesse tinte in chiaro e in scuro */
.rim { fill: var(--logo-red); }
.plate { fill: var(--logo-plate); }
.red { fill: var(--logo-red); }
.orange { fill: var(--logo-orange); }
.cyan { fill: var(--logo-cyan); }
.yellow { fill: var(--logo-yellow); }
.text { display: grid; line-height: 1; }
.name { font-family: var(--font-display); font-size: var(--text-lg); font-weight: var(--weight-brand); letter-spacing: 0.05em; }
.by { margin-top: 2px; color: var(--fg-subtle); font-size: var(--text-2xs); opacity: 0.85; }
</style>
