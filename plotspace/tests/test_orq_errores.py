"""El chat de Jarvis dice POR QUÉ falló el motor suscripción (claude -p).

Antes, cuando el CLI devolvía un `result` con is_error (sin sesión, sin cuota),
/chat-stream mostraba «el CLI terminó sin resultado utilizable» y el motivo real
se perdía. Ahora `orq_cli.diagnosticar` clasifica el texto del CLI y el chat
recibe un mensaje accionable + `codigo` (la UI suma el botón: abrir Cuentas,
instalar el CLI).
"""
import json
import os
import tempfile
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.core import orq_cli
from plotspace.core.database import get_db
from plotspace.routers import orchestrator
from plotspace.tests._harness import fresh_db


@pytest.mark.parametrize('texto,codigo', [
    ('Invalid API key · Please run /login', 'login'),
    ('Not logged in · Please run /login', 'login'),
    ('OAuth token has expired. Please obtain a new token or refresh your existing token.', 'login'),
    ('API Error: 401 {"type":"error","error":{"type":"authentication_error"}}', 'login'),
    ('Claude AI usage limit reached|1760300000', 'limite'),
    ("You've hit your limit · resets 3pm (America/Argentina/Buenos_Aires)", 'limite'),
    ('5-hour limit reached ∙ resets 11pm', 'limite'),
    ('API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', 'sobrecarga'),
    ('el CLI `claude` no está instalado o no está en el PATH', 'no_instalado'),
    ('el orquestador tardó más de 240s — corté la llamada', 'otro'),
    ('', 'otro'),
])
def test_diagnosticar(texto, codigo):
    assert orq_cli.diagnosticar(texto)['codigo'] == codigo


def test_el_reset_del_limite_se_extrae():
    assert orq_cli.diagnosticar("You've hit your limit · resets 3pm (America/Argentina/Buenos_Aires)")['reset'] \
        == '3pm (America/Argentina/Buenos_Aires)'
    assert orq_cli.diagnosticar('Claude AI usage limit reached|1760300000')['reset']   # hora local HH:MM
    assert orq_cli.diagnosticar('Invalid API key · Please run /login')['reset'] is None


def test_error_de_resultado_conserva_el_texto_del_cli():
    e = orq_cli.error_de_resultado({'texto': 'Invalid API key · Please run /login', 'error': True})
    assert e.codigo == 'login' and 'Please run /login' in str(e)
    assert orq_cli.error_de_resultado(None).codigo == 'otro'


def test_mensajes_accionables(monkeypatch):
    monkeypatch.setattr(orchestrator, 'L', lambda es, en: es)
    msg = orchestrator._mensaje_error_orq(orq_cli.OrqCliError('x: Invalid API key · Please run /login'))
    assert 'Ajustes → Cuentas' in msg
    msg = orchestrator._mensaje_error_orq(orq_cli.OrqCliError("x: You've hit your limit · resets 3pm"))
    assert 'límite de uso' in msg and 'se libera 3pm' in msg
    msg = orchestrator._mensaje_error_orq(orq_cli.OrqCliError('algo que no conocemos'))
    assert msg.startswith('Orquestador (suscripción): ') and 'algo que no conocemos' in msg


def _proyecto(ruta):
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO projects (nombre, ruta, fecha_creacion, ultimo_acceso) '
                           'VALUES (?, ?, ?, ?)', ('p', ruta, now, now))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _claude_falso(tmp_path, resultado):
    falso = tmp_path / 'claude'
    falso.write_text('#!/usr/bin/env python3\nimport sys, json\nsys.stdin.read()\n'
                     f'print(json.dumps({json.dumps(resultado)}))\n')
    falso.chmod(0o755)
    return str(falso)


def _cliente(monkeypatch, bin_):
    monkeypatch.setattr(orchestrator, 'ORQUESTADOR_MOTOR', 'suscripcion')
    monkeypatch.setattr(orq_cli, 'BIN', bin_)
    app = FastAPI()
    app.include_router(orchestrator.router)
    return TestClient(app)


def test_stream_sin_sesion_dice_por_que_y_trae_codigo(tmp_path, monkeypatch):
    fresh_db()
    pid = _proyecto(tempfile.mkdtemp())
    c = _cliente(monkeypatch, _claude_falso(tmp_path, {
        'type': 'result', 'subtype': 'success', 'is_error': True,
        'result': 'Invalid API key · Please run /login', 'usage': {}}))
    r = c.post('/api/orchestrator/chat-stream', json={'project_id': pid, 'message': 'hola'})
    eventos = [json.loads(l[5:]) for l in r.text.split('\n\n') if l.startswith('data:')]
    err = [e for e in eventos if e['type'] == 'error']
    assert err, eventos
    assert err[0]['codigo'] == 'login'
    assert 'sin resultado utilizable' not in err[0]['detail']
    assert 'Cuentas' in err[0]['detail'] or 'Accounts' in err[0]['detail']


def test_chat_sin_stream_devuelve_detalle_estructurado(tmp_path, monkeypatch):
    fresh_db()
    pid = _proyecto(tempfile.mkdtemp())
    c = _cliente(monkeypatch, _claude_falso(tmp_path, {
        'type': 'result', 'is_error': True,
        'result': "You've hit your limit · resets 3pm", 'usage': {}}))
    r = c.post('/api/orchestrator/chat', json={'project_id': pid, 'message': 'hola'})
    assert r.status_code == 502
    det = r.json()['detail']
    assert det['error'] == 'limite' and '3pm' in det['message']


def test_cli_no_instalado_es_el_409_no_cli(monkeypatch):
    # El guard mira el MISMO binario que se ejecuta (orq_cli.BIN), no `claude`
    # a secas: sin esto el resultado dependía de si la máquina lo tenía instalado.
    fresh_db()
    pid = _proyecto(tempfile.mkdtemp())
    c = _cliente(monkeypatch, os.path.join(tempfile.mkdtemp(), 'no-existe-claude'))
    r = c.post('/api/orchestrator/chat-stream', json={'project_id': pid, 'message': 'hola'})
    assert r.status_code == 409 and r.json()['detail']['error'] == 'no_cli', r.text
