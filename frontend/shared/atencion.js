// JARVIS — Atención entre proyectos (lógica pura; la usa shell/workspace.js).
//
// El server (core/atencion.py) lleva qué agentes te necesitan en CADA proyecto:
// 'espera' (pregunta a la vista), 'bloqueado' (TASK_BLOCKED), 'error'
// (TASK_ERROR), 'limite' (sin cuota y sin otra cuenta a la que rotar). Llega por
// nivel en GET /api/projects/working (`atencion`) y como aviso nuevo por el WS
// global `proyecto_atencion`. Acá: qué filas de la franja marcar y qué decir.
(function (global) {
  'use strict';

  const FRASES = {
    espera:    '{n} espera tu respuesta',
    bloqueado: '{n} se bloqueó',
    error:     '{n} terminó con error',
    limite:    '{n} se quedó sin cuota de uso',
  };

  /** Frase (en español: la traduce `t`) para UN aviso. */
  function frase(item, t) {
    const tr = typeof t === 'function' ? t : (s) => s;
    const base = FRASES[item && item.motivo] || FRASES.espera;
    const n = (item && (item.terminal_nombre || (item.terminal_id != null ? `Terminal ${item.terminal_id}` : ''))) || tr('Un agente');
    return tr(base).replace('{n}', n);
  }

  /** {pid: [avisos]} SIN el proyecto que estás mirando (ahí ya avisan sus cards). */
  function porProyecto(atencion, activoId) {
    const out = {};
    if (!atencion || typeof atencion !== 'object') return out;
    for (const [pid, lista] of Object.entries(atencion)) {
      if (String(pid) === String(activoId)) continue;
      if (Array.isArray(lista) && lista.length) out[String(pid)] = lista;
    }
    return out;
  }

  /** Tooltip de la fila: una línea por agente. */
  function resumen(lista, t) {
    return (Array.isArray(lista) ? lista : []).map((it) => frase(it, t)).join('\n');
  }

  /** ¿Avisar (sonido/toast) un `proyecto_atencion`? Solo si es de OTRO proyecto. */
  function debeAvisar(evento, activoId) {
    return !!evento && evento.project_id != null && String(evento.project_id) !== String(activoId);
  }

  const pure = { frase, porProyecto, resumen, debeAvisar, FRASES };
  global.JarvisAtencion = Object.assign(global.JarvisAtencion || {}, { _pure: pure, ...pure });
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
