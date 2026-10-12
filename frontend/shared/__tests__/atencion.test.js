// Atención entre proyectos: qué filas de la franja se marcan y qué se dice.
'use strict';
const assert = require('assert');
const A = require('../atencion.js');

// Frases por motivo, con el nombre de la terminal (o su id, o «Un agente»)
assert.strictEqual(A.frase({ motivo: 'espera', terminal_nombre: 'Backend' }), 'Backend espera tu respuesta');
assert.strictEqual(A.frase({ motivo: 'bloqueado', terminal_nombre: 'Backend' }), 'Backend se bloqueó');
assert.strictEqual(A.frase({ motivo: 'error', terminal_nombre: 'Backend' }), 'Backend terminó con error');
assert.strictEqual(A.frase({ motivo: 'limite', terminal_nombre: 'Backend' }), 'Backend se quedó sin cuota de uso');
assert.strictEqual(A.frase({ motivo: 'espera', terminal_id: 9 }), 'Terminal 9 espera tu respuesta');
assert.strictEqual(A.frase({ motivo: 'raro' }), 'Un agente espera tu respuesta');
// La traducción se aplica a la plantilla, no al nombre
const en = { '{n} se bloqueó': '{n} is blocked', 'Un agente': 'An agent' };
assert.strictEqual(A.frase({ motivo: 'bloqueado', terminal_nombre: 'Se bloqueó' }, (s) => en[s] || s), 'Se bloqueó is blocked');

// El proyecto que estás mirando no se marca (ahí avisan sus cards)
const at = { 1: [{ motivo: 'espera', terminal_nombre: 'A' }], 2: [{ motivo: 'error', terminal_nombre: 'B' }], 3: [] };
assert.deepStrictEqual(Object.keys(A.porProyecto(at, 1)), ['2']);
assert.deepStrictEqual(Object.keys(A.porProyecto(at, '2')), ['1']);
assert.deepStrictEqual(A.porProyecto(null, 1), {});

// Tooltip: una línea por agente
assert.strictEqual(A.resumen([{ motivo: 'espera', terminal_nombre: 'A' }, { motivo: 'limite', terminal_nombre: 'B' }]),
  'A espera tu respuesta\nB se quedó sin cuota de uso');
assert.strictEqual(A.resumen(undefined), '');

// Aviso (sonido/toast): solo si es de OTRO proyecto
assert.strictEqual(A.debeAvisar({ project_id: 2 }, 1), true);
assert.strictEqual(A.debeAvisar({ project_id: 2 }, '2'), false);
assert.strictEqual(A.debeAvisar({}, 1), false);
assert.strictEqual(A.debeAvisar(null, 1), false);

console.log('atencion OK');
