"""El editor standalone (`/editor`, Monaco en una pestaña aparte) se retiró: el
editor de código es el deslizante del workspace. Un marcador viejo a
`/editor?project=N` no puede quedar en 404: redirige a ese proyecto.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db


def _client() -> TestClient:
    fresh_db()
    import plotspace.main as main
    return TestClient(main.app)


def test_editor_viejo_redirige_al_proyecto():
    r = _client().get("/editor?project=7", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers['location'] == '/workspace?id=7'


def test_editor_viejo_sin_proyecto_o_con_basura_va_al_workspace():
    c = _client()
    for q in ('', '?project=', '?project=../x', '?project=1%3Cscript%3E'):
        r = c.get('/editor' + q, follow_redirects=False)
        assert r.status_code == 307 and r.headers['location'] == '/workspace', q


def test_el_archivo_standalone_ya_no_existe():
    assert not os.path.exists(os.path.join(_ROOT, 'frontend', 'shell', 'editor-standalone.html'))
    assert not os.path.exists(os.path.join(_ROOT, 'frontend', 'sections', 'editor', 'editor.js'))
