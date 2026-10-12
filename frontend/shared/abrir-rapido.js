// JARVIS — «Ir a archivo» (Ctrl+Shift+P) y apertura de `archivo:línea`.
//
// Reemplaza a la paleta del editor Monaco del dock (retirado): esa paleta abría
// los archivos en un editor que ya no se veía. Esta busca en el árbol del
// proyecto con el mismo fuzzy y abre en el editor deslizante (editor-slide).
// También normaliza rutas para `JarvisAbrirArchivo` (links `src/app.py:42` de
// las terminales, «Abrir» de Review y de Extensiones).
(function (global) {
  'use strict';

  /** Ruta relativa al proyecto, o null si es absoluta y cae FUERA de `raiz`. Puro. */
  function normalizarRuta(ruta, raiz) {
    let p = String(ruta || '').trim().replace(/^file:\/\//, '');
    if (!p) return null;
    const base = String(raiz || '').replace(/\/+$/, '');
    if (p.startsWith('/')) {
      if (base && (p === base || p.startsWith(base + '/'))) p = p.slice(base.length + 1);
      else return null;
    }
    p = p.replace(/^(\.\/)+/, '').replace(/^\/+/, '');
    return p || null;
  }

  /** Árbol de /files/tree → [{name, path}] de los ARCHIVOS. Puro. */
  function aplanar(nodos) {
    const out = [];
    (function walk(ns) {
      for (const n of ns || []) {
        if (n.type === 'file') out.push({ name: n.name, path: n.path });
        else if (n.children) walk(n.children);
      }
    })(nodos);
    return out;
  }

  /** Subsecuencia difusa: null si no matchea; score menor = mejor. Premia
   *  contigüidad, inicio de palabra (tras / _ - .) y match temprano. Puro. */
  function fuzzy(q, texto) {
    if (!q) return { score: 0, posiciones: [] };
    const lt = String(texto).toLowerCase(), lq = String(q).toLowerCase();
    const posiciones = [];
    let ti = 0, score = 0, prev = -2;
    for (let qi = 0; qi < lq.length; qi++) {
      const found = lt.indexOf(lq[qi], ti);
      if (found === -1) return null;
      score += found === prev + 1 ? 1 : (found - prev) + 5;
      const antes = found > 0 ? lt[found - 1] : '/';
      if (antes === '/' || antes === '_' || antes === '-' || antes === '.') score -= 3;
      posiciones.push(found);
      prev = found;
      ti = found + 1;
    }
    return { score: score + posiciones[0], posiciones };
  }

  /** Los mejores `max` archivos para la consulta. Puro. */
  function filtrar(q, archivos, max = 200) {
    const res = [];
    for (const a of archivos || []) {
      const m = fuzzy(q, a.path);
      if (m) res.push({ ...a, score: m.score, posiciones: m.posiciones });
    }
    res.sort((x, y) => x.score - y.score || x.path.length - y.path.length);
    return res.slice(0, max);
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** HTML del texto con las posiciones resaltadas (todo escapado). Puro. */
  function resaltar(texto, posiciones) {
    const set = new Set(posiciones || []);
    let out = '';
    for (let i = 0; i < texto.length; i++) {
      out += set.has(i) ? `<span class="ar-hl">${esc(texto[i])}</span>` : esc(texto[i]);
    }
    return out;
  }

  const pure = { normalizarRuta, aplanar, fuzzy, filtrar, resaltar };
  global.JarvisAbrirRapido = Object.assign(global.JarvisAbrirRapido || {}, { _pure: pure, ...pure });
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
  if (typeof document === 'undefined') return;

  // ── Navegador: la paleta ────────────────────────────────────────────────
  const _t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);
  let _overlay = null;

  function cerrar() {
    if (_overlay) { _overlay.remove(); _overlay = null; }
  }

  /** Abre la paleta sobre los archivos del proyecto; `alElegir(path)` al confirmar. */
  async function abrir(projectId, alElegir) {
    cerrar();
    if (projectId == null) return;
    const overlay = document.createElement('div');
    overlay.className = 'ar-overlay';
    overlay.innerHTML = `<div class="ar-modal" role="dialog" aria-label="${esc(_t('Ir a archivo'))}">
        <input class="ar-input" type="text" spellcheck="false" autocomplete="off" placeholder="${esc(_t('Ir a archivo…'))}">
        <div class="ar-list" role="listbox"><div class="ar-empty">${esc(_t('Cargando…'))}</div></div>
      </div>`;
    document.body.appendChild(overlay);
    _overlay = overlay;
    const input = overlay.querySelector('.ar-input');
    const lista = overlay.querySelector('.ar-list');
    input.focus();

    let archivos = [], items = [], activo = 0;
    const pintar = () => {
      if (!items.length) { lista.innerHTML = `<div class="ar-empty">${esc(_t('Sin resultados'))}</div>`; return; }
      lista.innerHTML = items.map((it, i) =>
        `<div class="ar-item${i === activo ? ' activo' : ''}" role="option" data-i="${i}">` +
        `<span class="ar-nombre">${esc(it.name)}</span>` +
        `<span class="ar-ruta">${resaltar(it.path, it.posiciones)}</span></div>`).join('');
      lista.querySelector('.ar-item.activo')?.scrollIntoView({ block: 'nearest' });
    };
    const refiltrar = () => { items = filtrar(input.value.trim(), archivos); activo = 0; pintar(); };
    const elegir = (it) => { if (!it) return; cerrar(); try { alElegir?.(it.path); } catch (_) {} };

    input.addEventListener('input', refiltrar);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrar(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) { activo = (activo + 1) % items.length; pintar(); } }
      else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) { activo = (activo - 1 + items.length) % items.length; pintar(); } }
      else if (e.key === 'Enter') { e.preventDefault(); elegir(items[activo]); }
    });
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) cerrar(); });
    lista.addEventListener('click', (e) => {
      const row = e.target.closest('.ar-item');
      if (row) elegir(items[+row.dataset.i]);
    });

    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(projectId)}/files/tree`);
      const data = r.ok ? await r.json() : null;
      archivos = aplanar(data?.children);
    } catch (_) { archivos = []; }
    if (_overlay === overlay) refiltrar();
  }

  Object.assign(global.JarvisAbrirRapido, { abrir, cerrar, abierto: () => !!_overlay });
})(typeof window !== 'undefined' ? window : globalThis);
