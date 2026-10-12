"""Atención entre proyectos (core/atencion.py).

Los avisos de un agente (espera respuesta, se bloqueó, falló, sin cuota) iban
SOLO por el WS de su proyecto: mirando otro proyecto no llegaban nunca. Ahora
un listener los lleva por proyecto, los anuncia GLOBAL (`proyecto_atencion`) y
los expone por nivel en GET /api/projects/working (`atencion`).
"""
import asyncio
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.core import atencion
from plotspace.core.database import get_db
from plotspace.core.events import EventBroadcaster
from plotspace.tests._harness import fresh_db


def _proyecto(nombre='Tienda'):
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO projects (nombre, ruta, fecha_creacion, ultimo_acceso) '
                           'VALUES (?, ?, ?, ?)', (nombre, '/tmp/x', now, now))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _terminal(pid, nombre='Backend', activa=1):
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion) '
                           'VALUES (?, ?, ?, ?, ?)', (pid, nombre, 'claude', activa, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


ACTIVA = {'pid': 7, 'nombre': 'Backend', 'activa': True}


def test_clasificar():
    assert atencion.clasificar({'type': 'agente_espera', 'terminal_id': 3}) == (3, 'espera')
    assert atencion.clasificar({'type': 'limite_sin_cuenta', 'terminal_id': '3'}) == (3, 'limite')
    assert atencion.clasificar({'type': 'task_event', 'event': 'TASK_BLOCKED', 'terminal_id': 3}) == (3, 'bloqueado')
    assert atencion.clasificar({'type': 'task_event', 'event': 'TASK_ERROR', 'terminal_id': 3}) == (3, 'error')
    assert atencion.clasificar({'type': 'task_event', 'event': 'TASK_DONE', 'terminal_id': 3}) == (3, None)
    assert atencion.clasificar({'type': 'agente_trabajando', 'terminal_id': 3}) == (3, None)
    assert atencion.clasificar({'type': 'agente_termino', 'terminal_id': 3}) == (3, None)
    # Ajenos: no tocan nada
    assert atencion.clasificar({'type': 'mailbox_aviso', 'terminal_id': 3}) == (None, '')
    assert atencion.clasificar({'type': 'live_update'}) == (None, '')
    assert atencion.clasificar({'type': 'proyecto_atencion', 'terminal_id': 3}) == (None, '')


def test_aviso_nuevo_se_anuncia_una_vez_y_se_limpia_al_retomar():
    atencion.reset()
    nuevo = atencion.aplicar({'type': 'agente_espera', 'terminal_id': 3}, ACTIVA)
    assert nuevo == {'project_id': 7, 'terminal_id': 3, 'terminal_nombre': 'Backend',
                     'motivo': 'espera', 'detalle': ''}
    # agent_watch puede re-emitir lo mismo: no se vuelve a anunciar
    assert atencion.aplicar({'type': 'agente_espera', 'terminal_id': 3}, ACTIVA) is None
    assert atencion.pendientes(activas={3})['7'][0]['motivo'] == 'espera'
    # Retomó: deja de necesitarte
    atencion.aplicar({'type': 'agente_trabajando', 'terminal_id': 3})
    assert atencion.pendientes(activas={3}) == {}


def test_cambio_de_motivo_se_anuncia_y_lleva_el_detalle():
    atencion.reset()
    atencion.aplicar({'type': 'agente_espera', 'terminal_id': 3}, ACTIVA)
    nuevo = atencion.aplicar({'type': 'task_event', 'event': 'TASK_ERROR', 'terminal_id': 3,
                              'motivo': 'los tests no corren'}, ACTIVA)
    assert nuevo['motivo'] == 'error' and nuevo['detalle'] == 'los tests no corren'
    atencion.aplicar({'type': 'task_event', 'event': 'TASK_DONE', 'terminal_id': 3})
    assert atencion.pendientes(activas={3}) == {}


def test_terminal_inactiva_o_cerrada_no_queda_pendiente():
    atencion.reset()
    assert atencion.aplicar({'type': 'agente_espera', 'terminal_id': 4},
                            {'pid': 7, 'nombre': 'X', 'activa': False}) is None
    assert atencion.aplicar({'type': 'agente_espera', 'terminal_id': 4}, None) is None
    atencion.aplicar({'type': 'agente_espera', 'terminal_id': 5}, ACTIVA)
    assert atencion.pendientes(activas=set()) == {}          # se cerró sin avisar → filtrada
    atencion.olvidar_terminal(5)
    assert atencion.pendientes(activas={5}) == {}


class _WS:
    def __init__(self):
        self.recibidos = []

    async def send_json(self, data):
        self.recibidos.append(data)


def test_el_aviso_llega_a_quien_mira_otro_proyecto(monkeypatch):
    fresh_db()
    atencion.reset()
    pid_a, pid_b = _proyecto('A'), _proyecto('Tienda')
    tid = _terminal(pid_b, 'Backend')
    bc = EventBroadcaster()
    monkeypatch.setattr('plotspace.core.events.broadcaster', bc)
    bc.escuchar(atencion._on_evento)
    mirando_a = _WS()
    bc._conns[pid_a] = [mirando_a]

    asyncio.run(bc.broadcast(pid_b, {'type': 'agente_espera', 'terminal_id': tid,
                                     'terminal_nombre': 'Backend'}))

    avisos = [d for d in mirando_a.recibidos if d.get('type') == 'proyecto_atencion']
    assert len(avisos) == 1, mirando_a.recibidos
    assert avisos[0]['project_id'] == pid_b
    assert avisos[0]['project_nombre'] == 'Tienda'
    assert avisos[0]['terminal_nombre'] == 'Backend'
    assert avisos[0]['motivo'] == 'espera'


def test_working_expone_la_atencion_por_proyecto():
    from plotspace.routers import projects
    fresh_db()
    atencion.reset()
    pid = _proyecto()
    tid = _terminal(pid)
    atencion.aplicar({'type': 'limite_sin_cuenta', 'terminal_id': tid, 'tipo_ia': 'claude'},
                     {'pid': pid, 'nombre': 'Backend', 'activa': True})
    app = FastAPI()
    app.include_router(projects.router)
    data = TestClient(app).get('/api/projects/working').json()
    fila = data['atencion'][str(pid)][0]
    assert fila['terminal_id'] == tid and fila['motivo'] == 'limite' and fila['detalle'] == 'claude'
