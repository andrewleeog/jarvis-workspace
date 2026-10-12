"""Review: "commitear lo de un agente" sube SOLO lo de ese agente.

Antes `/review/commit` hacía `git add <archivo>` + `git commit -- <archivo>`:
archivos ENTEROS. Si otro agente tenía cambios a medio hacer en el mismo
archivo, viajaban en el commit con el mensaje del primero. Ahora, con
`agente`, un archivo compartido se stagea por HUNK con la provenance real
(qué texto escribió cada uno — la misma lógica que `jv commit`), bajo el mismo
lock que `commit_propio` (.git/jarvis-commit.lock), y el commit lleva el
trailer `Jarvis-Agent:`.
"""
import os
import subprocess
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.core import provenance
from plotspace.routers import review
from plotspace.tests._harness import fresh_db, make_client_and_project

ORIGINAL = """def a():
    return 'original de la funcion a'


def b():
    return 'original de la funcion b'
"""


def _git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                          capture_output=True).stdout


def _escribir(d, rel, texto):
    with open(os.path.join(d, rel), 'w', encoding='utf-8') as f:
        f.write(texto)


def _repo():
    fresh_db()
    provenance.reset()
    d = tempfile.mkdtemp()
    _git(d, 'init', '-q', '-b', 'main')
    _git(d, 'config', 'user.email', 't@jarvis.local')
    _git(d, 'config', 'user.name', 'T')
    _escribir(d, 'app.py', ORIGINAL)
    _escribir(d, 'otro.py', 'x = 1\n')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'init')
    app = FastAPI()
    app.include_router(review.router)
    _, pid = make_client_and_project(d)
    return TestClient(app), pid, d


def _dos_agentes_en_app(pid, d):
    """El agente 1 cambia a(), el agente 2 cambia b(): los dos sin commitear."""
    _escribir(d, 'app.py', ORIGINAL
              .replace("original de la funcion a", "cambio que hizo el agente uno")
              .replace("original de la funcion b", "cambio que hizo el agente dos"))
    provenance.registrar(pid, 1, 'Uno', 'app.py', 'write',
                         antes="return 'original de la funcion a'",
                         despues="return 'cambio que hizo el agente uno'")
    provenance.registrar(pid, 2, 'Dos', 'app.py', 'write',
                         antes="return 'original de la funcion b'",
                         despues="return 'cambio que hizo el agente dos'")


def test_archivo_compartido_sube_solo_los_hunks_del_agente():
    c, pid, d = _repo()
    _dos_agentes_en_app(pid, d)
    r = c.post(f'/api/projects/{pid}/review/commit',
               json={'archivos': ['app.py'], 'mensaje': 'feat: lo del uno', 'agente': 1})
    data = r.json()
    assert data['ok'] is True, data
    assert data['por_hunk'] == ['app.py']
    en_head = _git(d, 'show', 'HEAD:app.py')
    assert 'cambio que hizo el agente uno' in en_head
    assert 'cambio que hizo el agente dos' not in en_head, 'se llevó el trabajo del otro agente'
    # El cambio del agente dos sigue en el disco, sin commitear
    assert 'cambio que hizo el agente dos' in open(os.path.join(d, 'app.py')).read()
    assert 'app.py' in _git(d, 'status', '--porcelain')
    assert 'Jarvis-Agent: Uno (tid 1)' in _git(d, 'log', '-1', '--format=%B')


def test_archivo_de_un_solo_agente_va_entero():
    c, pid, d = _repo()
    _escribir(d, 'otro.py', 'x = 2  # cambio del agente uno\n')
    provenance.registrar(pid, 1, 'Uno', 'otro.py', 'write', antes='x = 1', despues='x = 2  # cambio del agente uno')
    r = c.post(f'/api/projects/{pid}/review/commit',
               json={'archivos': ['otro.py'], 'mensaje': 'fix: x', 'agente': 1})
    data = r.json()
    assert data['ok'] is True and data['por_hunk'] == [], data
    assert _git(d, 'show', 'HEAD:otro.py') == 'x = 2  # cambio del agente uno\n'
    assert _git(d, 'status', '--porcelain') == ''


def test_sin_nada_atribuible_no_commitea():
    c, pid, d = _repo()
    _dos_agentes_en_app(pid, d)
    head_antes = _git(d, 'rev-parse', 'HEAD')
    r = c.post(f'/api/projects/{pid}/review/commit',
               json={'archivos': ['app.py'], 'mensaje': 'feat: x', 'agente': 3})   # el 3 no escribió nada
    data = r.json()
    assert data['ok'] is False, data
    assert _git(d, 'rev-parse', 'HEAD') == head_antes
    assert _git(d, 'diff', '--cached', '--name-only') == ''   # el índice queda limpio


def test_lo_que_otro_dejo_en_el_indice_no_viaja():
    c, pid, d = _repo()
    _dos_agentes_en_app(pid, d)
    _escribir(d, 'otro.py', 'x = 99\n')
    _git(d, 'add', 'otro.py')                              # staged por otro
    r = c.post(f'/api/projects/{pid}/review/commit',
               json={'archivos': ['app.py'], 'mensaje': 'feat: uno', 'agente': 1})
    assert r.json()['ok'] is True, r.json()
    assert 'otro.py' not in _git(d, 'show', '--name-only', '--format=', 'HEAD')


def test_sin_agente_sigue_commiteando_archivos_enteros():
    c, pid, d = _repo()
    _escribir(d, 'otro.py', 'x = 3\n')
    r = c.post(f'/api/projects/{pid}/review/commit', json={'archivos': ['otro.py'], 'mensaje': 'fix: y'})
    assert r.json()['ok'] is True, r.json()
    assert _git(d, 'show', 'HEAD:otro.py') == 'x = 3\n'


def test_by_agent_marca_archivos_compartidos(monkeypatch):
    c, pid, d = _repo()
    _dos_agentes_en_app(pid, d)
    monkeypatch.setattr(review, '_duenos_para',
                        lambda p: [{'terminal_id': 1, 'nombre': 'Uno', 'archivos': ['app.py']},
                                   {'terminal_id': 2, 'nombre': 'Dos', 'archivos': []}])
    data = c.get(f'/api/projects/{pid}/review/by-agent').json()
    archivo = data['agentes'][0]['archivos'][0]
    assert archivo['path'] == 'app.py'
    assert archivo['tambien'] == ['Dos']


def test_lo_staged_ajeno_con_espacios_en_el_nombre_tampoco_viaja():
    c, pid, d = _repo()
    _dos_agentes_en_app(pid, d)
    _escribir(d, 'notas del otro.txt', 'borrador\n')
    _git(d, 'add', 'notas del otro.txt')
    r = c.post(f'/api/projects/{pid}/review/commit',
               json={'archivos': ['app.py'], 'mensaje': 'feat: uno', 'agente': 1})
    assert r.json()['ok'] is True, r.json()
    assert _git(d, 'show', '--name-only', '--format=', 'HEAD').split('\n')[0] == 'app.py'
    assert 'notas del otro.txt' not in _git(d, 'show', '--name-only', '--format=', 'HEAD')
