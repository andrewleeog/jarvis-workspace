// JARVIS — carga perezosa de Monaco (lo usa el diff de Review).
//
// Antes vivía dentro del editor Monaco del dock (sections/editor/editor.js),
// que se retiró: el editor de código es el deslizante (editor-slide). Review
// sigue mostrando los diffs con Monaco, así que la carga quedó acá, sola.
//
//   await window.JarvisMonaco.cargar()  → el global `monaco` (o null si falló)
//
// Define el tema 'jarvis-dark' desde los tokens de la apariencia ACTUAL en cada
// carga (un diff abierto después de cambiar de tema ya sale con el nuevo); con
// un tema claro la base es 'vs' en vez de 'vs-dark'.
(function (global) {
  'use strict';

  const VERSION = '0.44.0';
  const BASE = `https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/${VERSION}/min/vs`;

  /** Bytes RGBA (0-255) → '#rrggbb' o '#rrggbbaa' (si no es opaco). Puro. */
  function hexDeRGBA(r, g, b, a = 255) {
    const h = (n) => Math.max(0, Math.min(255, n | 0)).toString(16).padStart(2, '0');
    return '#' + h(r) + h(g) + h(b) + (a < 255 ? h(a) : '');
  }

  /** ¿Fondo claro? (luminancia relativa de un '#rrggbb'). Puro. */
  function esClaro(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(String(hex || ''));
    if (!m) return false;
    const [r, g, b] = [m[1], m[2], m[3]].map(x => parseInt(x, 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5;
  }

  /** Tema de Monaco a partir de colores ya resueltos a hex. Puro. */
  function tema(c) {
    return {
      base: esClaro(c.bg) ? 'vs' : 'vs-dark',
      inherit: true,
      rules: [],   // sintaxis intacta de la base
      colors: {
        'editor.background': c.bg,
        'editor.foreground': c.fg,
        'editorLineNumber.foreground': c.linea,
        'editorLineNumber.activeForeground': c.acento,
        'editor.selectionBackground': c.seleccion,
        'editor.lineHighlightBackground': c.lineaActiva,
        'editorCursor.foreground': c.cursor,
      },
    };
  }

  const pure = { hexDeRGBA, esClaro, tema };
  global.JarvisMonaco = Object.assign(global.JarvisMonaco || {}, { _pure: pure });
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
  if (typeof document === 'undefined') return;

  // Un token CSS resuelto a hex PINTANDO un píxel y leyéndolo: los tokens son
  // oklch()/color-mix() y `ctx.fillStyle` no los devuelve en hex (el editor viejo
  // leía fillStyle y caía SIEMPRE al color por defecto: el diff nunca seguía al tema).
  const _ctx = (() => {
    try {
      const c = document.createElement('canvas'); c.width = c.height = 1;
      return c.getContext('2d', { willReadFrequently: true });
    } catch (_) { return null; }
  })();
  function _token(nombre, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();
    if (!v || !_ctx) return fallback;
    try {
      _ctx.clearRect(0, 0, 1, 1);
      _ctx.fillStyle = fallback;   // si `v` no es un color válido, queda el de respaldo
      _ctx.fillStyle = v;
      _ctx.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = _ctx.getImageData(0, 0, 1, 1).data;
      return hexDeRGBA(r, g, b, a);
    } catch (_) { return fallback; }
  }
  function _colores() {
    return {
      bg: _token('--ob-bg-void', '#0b0b0b'),
      fg: _token('--ob-fg-1', '#e2e2e2'),
      linea: _token('--ob-fg-3', '#3a3a3a'),
      acento: _token('--ob-accent', '#7c3aed'),
      cursor: _token('--ob-accent-fg', '#a78bfa'),
      seleccion: _token('--ob-accent-24', '#7c3aed44'),
      lineaActiva: _token('--ob-bg-1', '#141414'),
    };
  }

  function _script(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error('No se pudo cargar ' + src));
      document.head.appendChild(s);
    });
  }

  let _promesa = null;
  async function cargar() {
    if (!global.monaco) {
      // Una sola carga en vuelo: todos los que llaman esperan la MISMA.
      _promesa = _promesa || (async () => {
        await _script(`${BASE}/loader.min.js`);
        global.require.config({ paths: { vs: BASE } });
        await new Promise((resolve, reject) => global.require(['vs/editor/editor.main'], resolve, reject));
      })();
      try { await _promesa; }
      catch (e) { _promesa = null; console.error('[monaco]', e); return null; }
    }
    try { global.monaco.editor.defineTheme('jarvis-dark', tema(_colores())); } catch (_) {}
    return global.monaco || null;
  }

  global.JarvisMonaco.cargar = cargar;
})(typeof window !== 'undefined' ? window : globalThis);
