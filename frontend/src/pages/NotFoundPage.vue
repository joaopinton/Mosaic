<script setup lang="ts">
import PageFrame from '../components/PageFrame.vue'
</script>

<template>
  <PageFrame title="Pagina non trovata" width="reading">
    <div class="empty">
      <h3>Qui non c'è niente</h3>
      <p>L'indirizzo non corrisponde a nessuna pagina di Mosaic.</p>
      <RouterLink to="/" class="btn primary">Vai a Esplora</RouterLink>
    </div>
  </PageFrame>
</template>
