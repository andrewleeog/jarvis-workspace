'use strict';
// Links `archivo:línea` de las terminales (terminal-links.js). Lo que motivó el
// módulo: una ruta ABSOLUTA larga que xterm parte en dos filas no era link,
// porque la detección miraba cada fila por separado.
const assert = require('node:assert');
const L = require('../terminal-links.js');

// Una fila, ruta relativa con línea y columna
let ls = L.linksArchivo(['error en src/app.py:25:3 revisalo'], 80, 10);
assert.strictEqual(ls.length, 1);
assert.deepStrictEqual([ls[0].ruta, ls[0].linea, ls[0].col], ['src/app.py', 25, 3]);
assert.deepStrictEqual(ls[0].range, { start: { x: 10, y: 10 }, end: { x: 24, y: 10 } });
assert.strictEqual(ls[0].texto, 'src/app.py:25:3');

// Sin número de línea no es link; dominios y URLs tampoco
assert.deepStrictEqual(L.linksArchivo(['mirá src/app.py y x.com:3000'], 80, 1), []);
assert.deepStrictEqual(L.linksArchivo(['http://localhost:3000/static/a.js:12'], 80, 1), []);

// Ruta absoluta partida en DOS filas (cols=20): un solo link, con el rango cruzando filas
const linea = 'ver /home/u/proyecto/src/app.py:30:5 ok';
const filas = [linea.slice(0, 20), linea.slice(20)];
ls = L.linksArchivo(filas, 20, 7);
assert.strictEqual(ls.length, 1, JSON.stringify(ls));
assert.strictEqual(ls[0].ruta, '/home/u/proyecto/src/app.py');
assert.deepStrictEqual([ls[0].linea, ls[0].col], [30, 5]);
assert.deepStrictEqual(ls[0].range.start, { x: 5, y: 7 });          // offset 4 → fila 7, col 5
assert.deepStrictEqual(ls[0].range.end, { x: 16, y: 8 });           // offset 35 → fila 8, col 16
assert.ok(L.tocaFila(ls[0], 7) && L.tocaFila(ls[0], 8) && !L.tocaFila(ls[0], 9));

// Fila intermedia recortada por translateToString(true) (termina en espacios):
// el relleno mantiene las columnas de la fila siguiente
ls = L.linksArchivo(['abc', 'src/x.js:9'], 10, 1);
assert.deepStrictEqual(ls[0].range.start, { x: 1, y: 2 });

// Varios links en la misma línea
ls = L.linksArchivo(['a.py:1 y b/c.ts:22'], 80, 1);
assert.deepStrictEqual(ls.map(l => [l.ruta, l.linea]), [['a.py', 1], ['b/c.ts', 22]]);

console.log('terminal-links OK');
