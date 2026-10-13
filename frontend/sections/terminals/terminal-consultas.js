// JARVIS — xterm.js NO contesta las consultas de terminal (las contesta tmux).
//
// Las CLIs preguntan cosas a la terminal al arrancar: «¿qué terminal sos?» (DA),
// «¿dónde está el cursor?» (DSR/CPR), «¿soportás el modo sincronizado?»
// (DECRQM), «¿de qué color es el texto/fondo?» (OSC 10/11). La terminal REAL del
// pane es tmux y contesta TODAS (medido en vivo, 2026-10-13). xterm.js acá es solo
// un ESPEJO de la pantalla, pero también contestaba: su respuesta salía por onData
// → WS → send-keys → llegaba a la CLI como si el usuario la hubiera TIPEADO, unos
// ms después de la de tmux. Claude Code la tomaba como texto: la letra suelta en
// el input al crear una terminal (el `c` final de `\x1b[?1;2c`).
//
// Dos capas: (1) el parser de xterm se queda con las consultas antes de que las
// conteste; (2) red de seguridad en onData: lo que es inequívocamente una
// respuesta (ningún teclado la produce) no viaja. CPR (`\x1b[1;2R`) NO entra en
// la red: es idéntico a Shift+F3 — esa la cubre solo la capa (1).
(function (global) {
  'use strict';

  const CSI_CONSULTAS = [
    { final: 'c' },                                     // DA1
    { prefix: '>', final: 'c' },                        // DA2
    { prefix: '=', final: 'c' },                        // DA3
    { final: 'n' },                                     // DSR (5n estado, 6n cursor)
    { prefix: '?', final: 'n' },                        // DSR DEC (?6n …)
    { intermediates: '$', final: 'p' },                 // DECRQM ANSI
    { prefix: '?', intermediates: '$', final: 'p' },    // DECRQM DEC privado
    { prefix: '>', final: 'q' },                        // XTVERSION
  ];
  const DCS_CONSULTAS = [{ intermediates: '$', final: 'q' }];   // DECRQSS
  const OSC_COLORES = [4, 10, 11, 12];   // paleta, texto, fondo, cursor

  /** ¿El dato de un OSC de color es una CONSULTA (`?`) y no un seteo? */
  function esConsultaOsc(data) {
    return String(data == null ? '' : data).split(';').some(p => p.trim() === '?');
  }

  /** Engancha los handlers en el parser de `term`. Devuelve cuántos registró. */
  function silenciar(term) {
    const p = term && term.parser;
    if (!p) return 0;
    let n = 0;
    try {
      for (const id of CSI_CONSULTAS) { p.registerCsiHandler(id, () => true); n++; }
      for (const id of DCS_CONSULTAS) { p.registerDcsHandler(id, () => true); n++; }
      for (const code of OSC_COLORES) { p.registerOscHandler(code, (data) => esConsultaOsc(data)); n++; }
    } catch (_) { /* xterm sin esa API: queda la red de seguridad de onData */ }
    return n;
  }

  // Respuestas que ningún teclado produce: DA (`\x1b[?1;2c`, `\x1b[>0;276;0c`),
  // DECRQM (`\x1b[?2026;0$y`), colores OSC (`\x1b]11;rgb:…\x1b\\` o BEL) y
  // DECRQSS (`\x1bP1$r…\x1b\\`). Una o varias pegadas en el mismo onData.
  const RE_RESPUESTA = /^(?:\x1b\[[?>=][\d;]*c|\x1b\[\??\d+;\d+\$y|\x1b\]\d+;(?:\d+;)?rgb:[0-9a-fA-F/]+(?:\x07|\x1b\\)|\x1bP[01]\$r[^\x1b]*\x1b\\)+$/;

  /** ¿`data` (un onData de xterm) es una respuesta automática de terminal? */
  function esRespuestaDeTerminal(data) {
    return RE_RESPUESTA.test(String(data || ''));
  }

  const api = { silenciar, esConsultaOsc, esRespuestaDeTerminal, CSI_CONSULTAS, DCS_CONSULTAS, OSC_COLORES };
  global.TerminalConsultas = Object.assign(global.TerminalConsultas || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
