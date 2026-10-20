<script setup lang="ts">
// Cornice di pagina (come features/workspace/PageFrame di sift): una sola <main> con il suo h1,
// testata con titolo e azioni, corpo scorrevole. Il focus passa all'h1 a ogni cambio pagina.
withDefaults(defineProps<{ title: string; subtitle?: string; width?: 'reading' | 'wide' | 'full' }>(), { width: 'wide' })
</script>

<template>
  <main id="main-content" class="page page-enter" tabindex="-1" aria-labelledby="page-title" :data-width="width">
    <header class="header">
      <div class="inner">
        <div class="titles">
          <h1 id="page-title" tabindex="-1">{{ title }}</h1>
          <p v-if="subtitle" class="sub">{{ subtitle }}</p>
        </div>
        <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
      </div>
    </header>
    <div class="body">
      <div class="scroll">
        <div class="inner"><slot /></div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.page { display: flex; flex: 1; flex-direction: column; min-height: 0; --frame-w: var(--content-wide); }
.page[data-width='reading'] { --frame-w: var(--content-reading); }
.page[data-width='full'] { --frame-w: none; }
.page:focus { outline: none; }
.header { padding: var(--space-5) var(--page-pad) var(--space-4); border-bottom: 1px solid var(--border-subtle); }
.inner { width: 100%; max-width: var(--frame-w); margin: 0 auto; }
.header .inner { display: flex; align-items: flex-end; gap: var(--space-4); }
.titles { flex: 1; min-width: 0; }
h1 { font-size: var(--text-2xl); font-weight: var(--weight-bold); letter-spacing: var(--tracking-display); }
h1:focus-visible { box-shadow: none; text-decoration: underline; text-decoration-color: var(--border-focus); }
.sub { margin-top: var(--space-1); color: var(--fg-muted); font-size: var(--text-sm); }
.actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.body { flex: 1; min-height: 0; display: flex; }
.scroll {
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: var(--space-4) var(--page-pad) var(--space-8);
}
@media (max-width: 40rem) {
  .header, .scroll { padding-inline: var(--space-4); }
  .header .inner { flex-direction: column; align-items: stretch; }
}
</style>
