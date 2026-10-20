<script setup lang="ts">
import { MotionConfig } from 'motion-v'
import AppShell from './components/AppShell.vue'
</script>

<template>
  <!-- reducedMotion="user": con prefers-reduced-motion le trasformazioni si spengono, resta la dissolvenza -->
  <MotionConfig reduced-motion="user">
    <AppShell />
  </MotionConfig>
</template>
