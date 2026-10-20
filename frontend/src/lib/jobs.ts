// Nomi leggibili dei job e delle fasi della pipeline.

const LABELS: Record<string, string> = {
  index: 'Indicizzazione',
  process: 'Metadati e miniature',
  analyze: 'Analisi AI',
  faces: 'Riconoscimento volti',
  moments: 'Album automatici',
  caption: 'Didascalie',
  rename: 'Rinomina',
  rollback: 'Annullamento rinomina',
  transcode: 'Conversione video',
  import: 'Importazione',
  drop: 'Aggiunta dei file rilasciati',
  backup: 'Backup dei dati manuali',
  cleanup: 'Pulizia',
  unlink: 'Scollegamento cartella',
}

export const jobLabel = (type: string): string => LABELS[type] ?? type

export const STATE_LABELS: Record<string, string> = {
  queued: 'in coda',
  running: 'in corso',
  done: 'completato',
  failed: 'non riuscito',
  cancelled: 'annullato',
}

/** Fasi che si possono riavviare da capo (POST /api/jobs/restart-type): i blocchi di metadati ripartono dall'indice. */
export const RESTARTABLE = new Set(['index', 'process', 'analyze', 'faces', 'moments', 'caption', 'rename'])
