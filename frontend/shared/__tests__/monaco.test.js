// Carga de Monaco para el diff de Review (shared/monaco.js): partes puras.
'use strict';
const assert = require('assert');
const M = require('../monaco.js');

assert.strictEqual(M.hexDeRGBA(11, 11, 11), '#0b0b0b');
assert.strictEqual(M.hexDeRGBA(255, 0, 16, 255), '#ff0010');
assert.strictEqual(M.hexDeRGBA(124, 58, 237, 64), '#7c3aed40');
assert.strictEqual(M.hexDeRGBA(300, -5, 0), '#ff0000');   // acotado

assert.strictEqual(M.esClaro('#f5f1e8'), true);
assert.strictEqual(M.esClaro('#0b0b0b'), false);

const c = { bg: '#f5f1e8', fg: '#222222', linea: '#999999', acento: '#7c3aed',
            cursor: '#7c3aed', seleccion: '#7c3aed44', lineaActiva: '#eeeeee' };
// Un tema claro no puede quedar con la base oscura (texto ilegible en el diff)
assert.strictEqual(M.tema(c).base, 'vs');
assert.strictEqual(M.tema({ ...c, bg: '#0b0b0b' }).base, 'vs-dark');
assert.strictEqual(M.tema(c).colors['editor.background'], '#f5f1e8');

console.log('monaco OK');
