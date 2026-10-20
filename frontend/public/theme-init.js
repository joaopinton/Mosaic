// Applica tema e sfondo del visore prima del primo render, per evitare il lampo di colore sbagliato.
(function () {
  try {
    var scheme = localStorage.getItem('mosaic.theme')
    if (scheme === 'light' || scheme === 'dark') document.documentElement.setAttribute('data-theme', scheme)
    if (localStorage.getItem('mosaic.viewer') === 'grey') document.documentElement.setAttribute('data-viewer', 'grey')
  } catch (e) {
    /* localStorage non disponibile: si resta sul tema di sistema */
  }
})()
