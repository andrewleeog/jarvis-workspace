// «Ir a archivo» y apertura de `archivo:línea` (shared/abrir-rapido.js).
'use strict';
const assert = require('assert');
const A = require('../abrir-rapido.js');

// Rutas: relativa tal cual; absoluta DENTRO del proyecto → relativa; fuera → null
assert.strictEqual(A.normalizarRuta('src/app.py', '/home/u/p'), 'src/app.py');
assert.strictEqual(A.normalizarRuta('./src/app.py', '/home/u/p'), 'src/app.py');
assert.strictEqual(A.normalizarRuta('/home/u/p/src/app.py', '/home/u/p/'), 'src/app.py');
assert.strictEqual(A.normalizarRuta('file:///home/u/p/a.js', '/home/u/p'), 'a.js');
assert.strictEqual(A.normalizarRuta('/etc/passwd', '/home/u/p'), null);
assert.strictEqual(A.normalizarRuta('/home/u/proyecto2/a.js', '/home/u/p'), null, 'prefijo de otra carpeta');
assert.strictEqual(A.normalizarRuta('', '/x'), null);

// Árbol → solo archivos
const arbol = [
  { type: 'dir', name: 'src', path: 'src', children: [
    { type: 'file', name: 'app.py', path: 'src/app.py' },
    { type: 'dir', name: 'ui', path: 'src/ui', children: [{ type: 'file', name: 'boton.js', path: 'src/ui/boton.js' }] },
  ] },
  { type: 'file', name: 'README.md', path: 'README.md' },
];
assert.deepStrictEqual(A.aplanar(arbol).map(f => f.path), ['src/app.py', 'src/ui/boton.js', 'README.md']);
assert.deepStrictEqual(A.aplanar(undefined), []);

// Fuzzy: subsecuencia; inicio de palabra y contigüidad ganan
assert.strictEqual(A.fuzzy('xyz', 'src/app.py'), null);
assert.ok(A.fuzzy('app', 'src/app.py'));
const res = A.filtrar('bot', A.aplanar(arbol));
assert.strictEqual(res[0].path, 'src/ui/boton.js');
assert.deepStrictEqual(res[0].posiciones, [7, 8, 9]);
// Sin consulta: todos, los más cortos primero
assert.deepStrictEqual(A.filtrar('', A.aplanar(arbol)).map(f => f.path), ['README.md', 'src/app.py', 'src/ui/boton.js']);

// Resaltado: escapa TODO (un nombre de archivo es dato, no HTML)
assert.strictEqual(A.resaltar('<a>.js', [1]), '&lt;<span class="ar-hl">a</span>&gt;.js');

console.log('abrir-rapido OK');
