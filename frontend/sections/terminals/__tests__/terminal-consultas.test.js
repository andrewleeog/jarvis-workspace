'use strict';
// xterm.js no contesta las consultas de terminal: las contesta tmux (la terminal
// real del pane). Si xterm también contestaba, su respuesta llegaba a la CLI como
// tipeo → la letra suelta en el input de Claude Code al crear una terminal.
const assert = require('node:assert');
const C = require('../terminal-consultas.js');

// Red de seguridad: respuestas reales capturadas en vivo (2026-10-13)
for (const r of ['\x1b[?1;2c', '\x1b[?2026;0$y', '\x1b[>0;276;0c',
                 '\x1b]10;rgb:d2d2/d0d0/d7d7\x1b\\', '\x1b]11;rgb:0000/0000/0000\x07',
                 '\x1b]4;1;rgb:cdcd/0000/0000\x1b\\', '\x1bP1$r0m\x1b\\',
                 '\x1b[?2026;0$y\x1b[?1;2c']) {
  assert.ok(C.esRespuestaDeTerminal(r), JSON.stringify(r));
}
// Lo que SÍ es tipeo nunca se filtra (incluido Shift+F3 = CSI 1;2 R, igual a un CPR)
for (const k of ['c', 'C', 'hola', '\r', '\x1b', '\x1b[A', '\x1b[C', '\x1b[1;2R', '\x1bOR',
                 '\x1b[I', '\x1b[O', '\x1b[200~pegado\x1b[201~', '\x1b[<64;10;5M', '?1;2c']) {
  assert.ok(!C.esRespuestaDeTerminal(k), JSON.stringify(k));
}
// Una respuesta con tipeo pegado no se descarta entera (perder teclas es peor)
assert.ok(!C.esRespuestaDeTerminal('\x1b[?1;2cx'));

// OSC de color: consulta (?) vs seteo
assert.ok(C.esConsultaOsc('?'));
assert.ok(C.esConsultaOsc('1;?'));            // OSC 4;1;?
assert.ok(!C.esConsultaOsc('rgb:ffff/0000/0000'));
assert.ok(!C.esConsultaOsc(''));

// silenciar: registra un handler por cada consulta y cada uno «se queda» con ella
const reg = { csi: [], dcs: [], osc: [] };
const term = { parser: {
  registerCsiHandler: (id, cb) => reg.csi.push([id, cb]),
  registerDcsHandler: (id, cb) => reg.dcs.push([id, cb]),
  registerOscHandler: (code, cb) => reg.osc.push([code, cb]),
} };
assert.strictEqual(C.silenciar(term), C.CSI_CONSULTAS.length + C.DCS_CONSULTAS.length + C.OSC_COLORES.length);
assert.ok(reg.csi.some(([id]) => id.final === 'c' && !id.prefix), 'DA1');
assert.ok(reg.csi.some(([id]) => id.final === 'n'), 'DSR / posición del cursor');
assert.ok(reg.csi.some(([id]) => id.prefix === '?' && id.intermediates === '$' && id.final === 'p'), 'DECRQM');
assert.ok(reg.csi.every(([, cb]) => cb() === true), 'todas las consultas CSI quedan atendidas (sin respuesta)');
const osc11 = reg.osc.find(([c]) => c === 11)[1];
assert.strictEqual(osc11('?'), true, 'consulta de fondo: se la queda');
assert.strictEqual(osc11('rgb:0000/0000/0000'), false, 'seteo de fondo: lo procesa xterm');
// Sin parser (xterm viejo) no explota
assert.strictEqual(C.silenciar({}), 0);
assert.strictEqual(C.silenciar(null), 0);

console.log('terminal-consultas OK');
