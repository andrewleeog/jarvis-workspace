// JARVIS — Links `archivo:línea` en las terminales (lógica pura).
//
// Los agentes imprimen `frontend/x.js:42` (o la ruta ABSOLUTA) a rolete: al
// clickearlos se abre el editor de Jarvis en esa línea (window.JarvisAbrirArchivo).
// Antes la detección miraba CADA FILA de pantalla por separado: una ruta larga
// que xterm partía en dos filas (lo normal con rutas absolutas en una card
// angosta) nunca era link. Acá se arma la línea LÓGICA (la fila + sus
// continuaciones envueltas) y cada link sale con su rango real, aunque cruce filas.
//
// Sólo extensiones de código conocidas, para no confundir dominios (`x.com:3000`).
// El wiring con xterm (registerLinkProvider) está en terminal.js.
(function (global) {
  'use strict';

  const RE_ARCHIVO_LINEA = /(?:^|[\s("'`\[{<])((?:\.\.?\/)?\/?[A-Za-z0-9_@][A-Za-z0-9_@./-]*\.(?:py|js|mjs|cjs|ts|tsx|jsx|css|scss|html?|jsonc?|md|markdown|ya?ml|toml|ini|cfg|conf|sh|bash|zsh|go|rs|java|kt|rb|php|cs|c|cc|cpp|h|hpp|swift|sql|vue|svelte|astro|lua|txt|env))(?::(\d+))(?::(\d+))?/g;

  /**
   * Links de una línea lógica.
   * @param {string[]} filas  textos de las filas que la forman (la primera y sus
   *                          continuaciones envueltas), cada una de `cols` celdas
   *                          salvo la última
   * @param {number} cols     ancho de la terminal
   * @param {number} y0       número de línea de buffer (1-based) de la primera fila
   * @returns {{ruta, linea, col, texto, range:{start:{x,y}, end:{x,y}}}[]}
   *          range en coordenadas de xterm (1-based, fin inclusive)
   */
  function linksArchivo(filas, cols, y0) {
    const ancho = Math.max(1, cols | 0);
    // Las filas intermedias ocupan el ancho completo: rellenar mantiene el mapa
    // offset → celda aunque translateToString haya recortado espacios.
    const texto = (filas || []).map((f, i, a) =>
      i < a.length - 1 ? String(f || '').padEnd(ancho).slice(0, ancho) : String(f || '')).join('');
    const celda = (off) => ({ x: (off % ancho) + 1, y: y0 + Math.floor(off / ancho) });
    const out = [];
    RE_ARCHIVO_LINEA.lastIndex = 0;
    let m;
    while ((m = RE_ARCHIVO_LINEA.exec(texto))) {
      const ruta = m[1];
      const full = ruta + ':' + m[2] + (m[3] ? ':' + m[3] : '');
      const ini = m.index + m[0].length - full.length;
      if (ini >= 2 && texto.slice(ini - 2, ini) === '//') continue;   // URL, no ruta
      out.push({
        ruta, texto: full,
        linea: parseInt(m[2], 10) || 1,
        col: m[3] ? parseInt(m[3], 10) : 1,
        range: { start: celda(ini), end: celda(ini + full.length - 1) },
      });
    }
    return out;
  }

  /** ¿El link toca la fila `y` (1-based)? xterm pide los links fila por fila. */
  const tocaFila = (link, y) => link.range.start.y <= y && y <= link.range.end.y;

  const api = { linksArchivo, tocaFila, RE_ARCHIVO_LINEA };
  global.TerminalLinks = Object.assign(global.TerminalLinks || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
