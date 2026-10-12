# JARVIS — Review Room.
# La decisión de ship con los ojos en el diff: qué archivos cambiaron en el
# proyecto (working tree vs HEAD), el diff completo coloreable, y el botón
# "Aprobar y commitear". Complementa al Reviewer del swarm: el agente revisa
# calidad; acá el HUMANO ve y decide el ship.

import os
import re
import subprocess
from fastapi import APIRouter, Body, HTTPException, Query

from plotspace.core import politica_commit
from plotspace.core.database import get_db

router = APIRouter(prefix="/api", tags=["review"])
# Todos los endpoints son `def` PLANOS (no async): llaman a git síncrono y
# FastAPI los corre en el threadpool — así un diff lento no congela el event loop.

MAX_DIFF_BYTES = 400_000       # cap defensivo: el diff GLOBAL se trunca para la UI
MAX_DIFF_FILE_BYTES = 80_000   # cap por archivo en la vista por-agente
MAX_UNTRACKED = 40             # tope de untracked a diffear (no reventar con node_modules)


def _ruta_proyecto(project_id: int) -> str:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return row['ruta']


GIT_TIMEOUT = 15          # un `git diff` enorme o un repo trabado no debe colgar el handler
GIT_COMMIT_TIMEOUT = 120  # el commit corre hooks (guard de propiedad, scanner de secretos)


def _git_full(cwd: str, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str, str]:
    """git síncrono (regla del proyecto: subprocess.run para git de control).
    Devuelve (rc, stdout, stderr) SEPARADOS: los warnings de git van a stderr y
    no deben mezclarse con la salida que se parsea (porcelain/numstat/...)."""
    try:
        # --no-optional-locks: el `status` de Review no toma index.lock (el commit de un agente fallaría).
        r = subprocess.run(['git', '--no-optional-locks', *args], cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout or ''), (r.stderr or '')
    except subprocess.TimeoutExpired:
        return -1, '', 'git timeout'
    except Exception as e:
        return -1, '', str(e)


def _git(cwd: str, *args: str) -> tuple[int, str]:
    """(rc, stdout) — SOLO stdout, que es lo que se parsea. Para mensajes de
    error con stderr usar _git_full."""
    rc, out, _ = _git_full(cwd, *args)
    return rc, out


def _error_git(out: str, err: str, defecto: str) -> str:
    return (err.strip() + '\n' + out.strip()).strip() or defecto


# ─── Atribución archivo→agente (lógica PURA, testeable sin git/DB) ────────────

def _norm_path(p: str) -> str:
    """Clave de comparación de paths entre git y agent_live: una CLI imprime
    './a.py' y git da 'a.py' — sin normalizar serían dos archivos distintos."""
    p = (p or '').strip()
    if p.startswith('./'):
        p = p[2:]
    return p


def duenos_desde_snapshot(snap: dict) -> list:
    """snapshot de agent_live → [{'terminal_id','nombre','archivos':[path,...]}]
    con SOLO los archivos de los que el agente es DUEÑO (no los que solo leyó).
    Esa es la fuente de atribución: el dueño de un archivo es quien lo escribió
    primero (ver plotspace/core/agent_live.py)."""
    out = []
    for a in snap.get('agentes', []):
        archivos = [f['path'] for f in a.get('archivos', []) if f.get('dueno')]
        out.append({'terminal_id': a['terminal_id'], 'nombre': a['nombre'],
                    'archivos': archivos})
    return out


def atribuir_cambios(archivos_cambiados: list, duenos: list) -> dict:
    """Agrupa los archivos cambiados del working tree por agente dueño.

    archivos_cambiados: [{'path':..., 'status':..., 'diff':...}] (dicts opacos;
        se agrupan por 'path' y se preservan tal cual — el 'diff' viaja adentro).
    duenos: [{'terminal_id':N, 'nombre':str, 'archivos':[path,...]}] — el dueño
        de cada archivo según el snapshot de agent_live.

    Devuelve {'agentes':[{'terminal_id','nombre','archivos':[...]}], 'sin_atribuir':[...]}.
    - Solo aparecen agentes con ≥1 archivo realmente cambiado.
    - Los agentes salen en el orden de `duenos`; sus archivos, en el orden de
      aparición en `archivos_cambiados`.
    - Un archivo cambiado sin dueño vigente va a 'sin_atribuir'."""
    indice = {}  # path normalizado → terminal_id (primer dueño gana)
    for d in duenos:
        for path in d.get('archivos', []):
            indice.setdefault(_norm_path(path), d['terminal_id'])

    buckets: dict = {}   # terminal_id → [file dict, ...]
    sin_atribuir = []
    for f in archivos_cambiados:
        tid = indice.get(_norm_path(f.get('path')))
        if tid is None:
            sin_atribuir.append(f)
        else:
            buckets.setdefault(tid, []).append(f)

    agentes = []
    emitidos = set()
    for d in duenos:
        tid = d['terminal_id']
        if tid in emitidos:
            continue
        emitidos.add(tid)
        archs = buckets.get(tid)
        if archs:
            agentes.append({'terminal_id': tid, 'nombre': d['nombre'],
                            'archivos': archs})
    return {'agentes': agentes, 'sin_atribuir': sin_atribuir}


def _duenos_para(project_id: int) -> list:
    """Mapeo dueño→archivos del proyecto, leído del estado VIVO de agent_live.
    Aislado en una función para poder mockearlo en los tests (seam)."""
    from plotspace.core import agent_live
    rows = agent_live._rows_de(project_id)
    snap = agent_live.snapshot(project_id, rows)
    return duenos_desde_snapshot(snap)


def _cap_diff(diff: str) -> str:
    """Trunca el diff de UN archivo para no inflar el payload de la UI."""
    b = diff.encode()
    if len(b) <= MAX_DIFF_FILE_BYTES:
        return diff
    return b[:MAX_DIFF_FILE_BYTES].decode(errors='ignore') + \
        '\n\n… (diff truncado para la UI — revisalo completo en una terminal)'


def _path_relativo_seguro(base: str, rel: str):
    """Anti-traversal: devuelve el path relativo LIMPIO si cae dentro del
    proyecto (léxico + symlinks), o None. Reusa el patrón de projects_files._safe_join."""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    if not rel:
        return None
    full = os.path.normpath(os.path.join(base, rel))
    base_norm = os.path.normpath(base)
    if full != base_norm and not full.startswith(base_norm + os.sep):
        return None
    real_full = os.path.realpath(full)
    real_base = os.path.realpath(base)
    if real_full != real_base and not real_full.startswith(real_base + os.sep):
        return None
    return os.path.relpath(full, base_norm)


@router.get("/projects/{project_id}/review/by-agent")
def review_por_agente(project_id: int):
    """Mismo diff que GET /review pero SEGMENTADO por agente: atribuye cada
    archivo cambiado a su dueño (snapshot de agent_live) y agrupa. Los archivos
    sin dueño vigente caen en 'sin_atribuir'."""
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")

    archivos_cambiados = []

    # Tracked: working tree vs HEAD (staged + unstaged), igual semántica que GET /review.
    # core.quotepath=false: paths unicode sin escapar a octal.
    _, name_status = _git(cwd, '-c', 'core.quotepath=false',
                          'diff', 'HEAD', '--name-status')
    for linea in name_status.splitlines():
        partes = linea.split('\t')
        if len(partes) < 2 or not partes[0]:
            continue
        status = partes[0][0]      # R100/C75 → 'R'/'C'; M/A/D/T quedan igual
        path = partes[-1].strip()  # en rename, el último campo es el destino
        if not path:
            continue
        _, diff = _git(cwd, 'diff', 'HEAD', '--no-color', '--', path)
        archivos_cambiados.append({'path': path, 'status': status,
                                   'diff': _cap_diff(diff)})

    # Untracked: archivos nuevos completos (status '??', como en git status --porcelain).
    _, porcelain = _git(cwd, '-c', 'core.quotepath=false', 'status', '--porcelain')
    untracked = [l[3:].strip() for l in porcelain.splitlines()
                 if l.startswith('??') and len(l) > 3]
    for path in untracked[:MAX_UNTRACKED]:
        _, diff = _git(cwd, 'diff', '--no-color', '--no-index', '/dev/null', path)
        archivos_cambiados.append({'path': path, 'status': '??',
                                   'diff': _cap_diff(diff)})

    # Artefacto (salida de herramientas: .workspace/, dist/, logs) vs. trabajo
    # real: el panel los separa para que no se commiteen por reflejo.
    for f in archivos_cambiados:
        f['clase'] = politica_commit.clasificar(f['path'])
    duenos = _duenos_para(project_id)
    res = atribuir_cambios(archivos_cambiados, duenos)
    # Archivos donde OTRO agente también escribió: la UI lo marca y el commit
    # por agente los sube por hunk (solo lo del dueño del grupo).
    try:
        frags = _fragmentos_por_archivo(project_id, [f['path'] for f in archivos_cambiados])
        nombres = {d['terminal_id']: d['nombre'] for d in duenos}
        def _otros(path, dueno):
            tids = [t for t in frags.get(_norm_path(path), {}) if t != dueno]
            return [nombres.get(t) or _nombre_agente(project_id, t) or f'terminal {t}' for t in tids]
        for g in res['agentes']:
            for f in g['archivos']:
                f['tambien'] = _otros(f['path'], g['terminal_id'])
        for f in res['sin_atribuir']:
            f['tambien'] = _otros(f['path'], None)
    except Exception:
        pass
    return res


# ─── Commit (por agente: solo SUS hunks, bajo el lock de commit_propio) ────────

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'scripts')


def _scripts():
    """hunks_propios / commit_propio viven en scripts/ (stdlib pura, los usan los
    agentes con `jv commit`): la MISMA lógica de stage por hunk y el MISMO lock."""
    import sys
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import commit_propio
    import hunks_propios
    return commit_propio, hunks_propios


def _fragmentos_por_archivo(pid, rels) -> dict:
    """{path: {tid: [texto escrito/reemplazado, ...]}} desde la provenance."""
    from plotspace.core import provenance
    quiero = {_norm_path(r) for r in rels}
    out: dict = {}
    for e in provenance.ediciones(pid=pid):
        p = _norm_path(e['path'])
        if p not in quiero:
            continue
        frs = out.setdefault(p, {}).setdefault(e['tid'], [])
        for t in (e['despues'], e['antes']):
            if t and t not in frs:
                frs.append(t)
    return out


def _nombre_agente(pid, tid) -> str:
    from plotspace.core import provenance
    for e in reversed(provenance.ediciones(pid=pid, tid=tid)):
        if e.get('nombre'):
            return e['nombre']
    try:
        conn = get_db()
        try:
            row = conn.execute('SELECT nombre FROM terminals WHERE id = ?', (tid,)).fetchone()
        finally:
            conn.close()
        if row and row['nombre']:
            return row['nombre']
    except Exception:
        pass
    return ''


def _dir_git(cwd) -> str:
    """Carpeta .git COMÚN del repo (en un worktree `.git` es un archivo): ahí vive
    el lock compartido con `commit_propio`, que en un repo normal es <raíz>/.git."""
    rc, out = _git(cwd, 'rev-parse', '--git-common-dir')
    d = out.strip() if rc == 0 else ''
    return os.path.normpath(os.path.join(cwd, d)) if d else os.path.join(cwd, '.git')


def _stage_hunks(cwd, archivo, mios, ajenos, hp):
    """Aplica al índice SOLO los hunks de `archivo` atribuibles a `mios`.
    (ok, motivo_si_no)."""
    rc, diff = _git(cwd, 'diff', '-U0', '--no-color', '--', archivo)
    if rc != 0 or not diff.strip():
        return False, 'sin cambios'
    parche = hp.filtrar_parche(diff, mios, ajenos)
    if not parche:
        return False, 'no hay cambios atribuibles a este agente (o están pegados a los de otro)'
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.patch', dir=_dir_git(cwd))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(parche)
        rc, out, err = _git_full(cwd, 'apply', '--cached', '--unidiff-zero', tmp)
        return (True, '') if rc == 0 else (False, _error_git(out, err, 'git apply falló'))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@router.post("/projects/{project_id}/review/commit")
def commit_por_agente(project_id: int, body: dict = Body(default=None)):
    """Commitea SOLO los archivos indicados. Nunca `git add -A`. Los hooks corren
    (sin --no-verify): el candado de propiedad/secretos sigue activo.

    Con `agente` (terminal_id: el grupo de Review de donde salió la selección),
    un archivo donde OTRO agente también tiene cambios sin commitear se stagea
    por HUNK con la provenance (lo que escribió cada uno) — antes viajaba entero
    con el trabajo a medio hacer del otro adentro — y el commit lleva el trailer
    `Jarvis-Agent:`. Todo bajo el lock de `commit_propio` (.git/jarvis-commit.lock)
    para no cruzarse con el `jv commit` de un agente."""
    body = body or {}
    archivos = body.get('archivos') or []
    mensaje = str(body.get('mensaje') or '').strip()
    agente = body.get('agente')
    try:
        agente = int(agente) if agente not in (None, '') else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="agente inválido")

    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")
    if not isinstance(archivos, list) or not archivos:
        raise HTTPException(status_code=400, detail="Faltan archivos para commitear")
    if not mensaje:
        raise HTTPException(status_code=400, detail="Falta el mensaje de commit")

    # Anti-traversal: cada path debe caer dentro del proyecto.
    rels = []
    for raw in archivos:
        rel = _path_relativo_seguro(cwd, str(raw))
        if rel is None:
            raise HTTPException(status_code=400,
                                detail=f"Ruta fuera del proyecto: {raw}")
        rels.append(rel)

    cp, hp = _scripts()
    trailer = cp.trailer_atribucion(_nombre_agente(project_id, agente), agente) if agente is not None else None
    frags = _fragmentos_por_archivo(project_id, rels) if agente is not None else {}
    nuevos = _untracked(cwd)
    por_hunk = [r for r in rels
                if r not in nuevos and any(t != agente for t in frags.get(_norm_path(r), {}))]
    por_archivo = [r for r in rels if r not in por_hunk]

    lock_path = os.path.join(_dir_git(cwd), 'jarvis-commit.lock')
    with open(lock_path, 'w') as lock:
        try:
            cp._tomar_lock(lock)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        try:
            if not por_hunk:
                # Camino de siempre: archivos enteros y commit acotado a esos paths
                # (`commit -- paths` no arrastra lo que otro dejó staged).
                rc, out, err = _git_full(cwd, 'add', '--', *rels)
                if rc != 0:
                    return {"ok": False, "error": _error_git(out, err, "git add falló")}
                args = ['commit', '-m', mensaje] + (['-m', trailer] if trailer else []) + ['--', *rels]
                rc, out, err = _git_full(cwd, *args, timeout=GIT_COMMIT_TIMEOUT)
                if rc != 0:
                    return {"ok": False, "error": _error_git(out, err, "git commit falló")}
                _, hash_ = _git(cwd, 'rev-parse', '--short', 'HEAD')
                return {"ok": True, "commit": hash_.strip(), "por_hunk": [], "omitidos": []}

            # Por hunk se commitea el ÍNDICE: lo que otro dejó staged viajaría →
            # se saca primero (queda en su árbol, como hace commit_propio).
            _, ya = _git(cwd, 'diff', '--cached', '--name-only', '-z')
            ajeno_staged = [f for f in ya.split('\0') if f and f not in por_archivo]
            if ajeno_staged:
                _git(cwd, 'reset', '-q', '--', *ajeno_staged)
            staged, omitidos = [], []
            for f in por_archivo:
                rc, out, err = _git_full(cwd, 'add', '--', f)
                if rc == 0:
                    staged.append(f)
                else:
                    omitidos.append({'path': f, 'motivo': _error_git(out, err, 'git add falló')})
            subidos_por_hunk = []
            for f in por_hunk:
                del_archivo = frags.get(_norm_path(f), {})
                mios = del_archivo.get(agente, [])
                ajenos = [t for tid, ts in del_archivo.items() if tid != agente for t in ts]
                ok, motivo = _stage_hunks(cwd, f, mios, ajenos, hp)
                if ok:
                    staged.append(f)
                    subidos_por_hunk.append(f)
                else:
                    omitidos.append({'path': f, 'motivo': motivo})
            if not staged:
                return {"ok": False, "omitidos": omitidos,
                        "error": "Nada de este agente para commitear: "
                                 + '; '.join(f"{o['path']} ({o['motivo']})" for o in omitidos)}
            args = ['commit', '-m', mensaje] + (['-m', trailer] if trailer else [])
            rc, out, err = _git_full(cwd, *args, timeout=GIT_COMMIT_TIMEOUT)
            if rc != 0:
                _git(cwd, 'reset', '-q', '--', *staged)    # no dejar el índice a medias
                return {"ok": False, "error": _error_git(out, err, "git commit falló")}
            _, hash_ = _git(cwd, 'rev-parse', '--short', 'HEAD')
            return {"ok": True, "commit": hash_.strip(), "por_hunk": subidos_por_hunk,
                    "omitidos": omitidos}
        finally:
            try:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:
                pass


GH_TIMEOUT = 60   # `gh pr create` habla con la API de GitHub


def _rels_seguros(cwd: str, archivos) -> list:
    """Valida la lista de paths del body: no vacía y todos DENTRO del proyecto.
    Un rename del porcelain llega como 'viejo -> nuevo': se expande a los dos."""
    if not isinstance(archivos, list) or not archivos:
        raise HTTPException(status_code=400, detail="Faltan archivos")
    rels = []
    for raw in archivos:
        for parte in str(raw).split(' -> '):
            rel = _path_relativo_seguro(cwd, parte)
            if rel is None:
                raise HTTPException(status_code=400,
                                    detail=f"Ruta fuera del proyecto: {raw}")
            if rel not in rels:
                rels.append(rel)
    return rels


def _untracked(cwd: str) -> set:
    """Paths untracked tal cual los da `git status --porcelain` (carpetas con '/')."""
    _, porcelain = _git(cwd, '-c', 'core.quotepath=false', 'status', '--porcelain')
    return {l[3:].strip().rstrip('/') for l in porcelain.splitlines()
            if l.startswith('??') and len(l) > 3}


@router.post("/projects/{project_id}/review/revert")
def revertir_archivos(project_id: int, body: dict = Body(default=None)):
    """Descarta los cambios de SOLO los archivos indicados y los deja como en
    HEAD. Tracked: `git restore --source=HEAD --staged --worktree` (un archivo
    agregado al índice que no está en HEAD se borra). Untracked: `git clean -fd`
    acotado a esos paths (nunca toca ignorados ni tracked).

    ANTES de tocar nada saca una foto del árbol sucio (refs/jarvis/wip/<ts>,
    índice temporal: no toca el de los agentes) y la devuelve en `respaldo`:
    /review/deshacer restaura desde ahí. Si la foto no sale, NO se descarta
    nada (antes era irreversible y podía llevarse trabajo de otro agente)."""
    body = body or {}
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")
    rels = _rels_seguros(cwd, body.get('archivos'))

    from plotspace.core import wip_snapshots
    respaldo = wip_snapshots.snapshot_wip(cwd)
    if not respaldo and wip_snapshots.ultimo_fallo():
        return {"ok": False,
                "error": "No pude guardar una copia antes de descartar, así que no descarté nada: "
                         + wip_snapshots.ultimo_fallo()}

    nuevos = _untracked(cwd)
    limpiar = [r for r in rels if r.rstrip('/') in nuevos]
    restaurar = [r for r in rels if r.rstrip('/') not in nuevos]

    if restaurar:
        rc, out, err = _git_full(cwd, 'restore', '--source=HEAD', '--staged',
                                 '--worktree', '--', *restaurar)
        if rc != 0:
            return {"ok": False, "error": _error_git(out, err, "git restore falló")}
    if limpiar:
        rc, out, err = _git_full(cwd, 'clean', '-f', '-d', '-q', '--', *limpiar)
        if rc != 0:
            return {"ok": False, "error": _error_git(out, err, "git clean falló")}

    return {"ok": True, "revertidos": restaurar, "borrados": limpiar, "respaldo": respaldo}


_RE_RESPALDO = re.compile(r'^refs/jarvis/wip/\d{8}-\d{6}$')


@router.post("/projects/{project_id}/review/deshacer")
def deshacer_descarte(project_id: int, body: dict = Body(default=None)):
    """Deshace un "Descartar": restaura los archivos indicados desde la foto que
    sacó /review/revert (`respaldo`). Solo acepta refs de Jarvis
    (refs/jarvis/wip/<ts>) y paths dentro del proyecto. Los archivos que no
    estaban en la foto se dejan como están."""
    body = body or {}
    cwd = _ruta_proyecto(project_id)
    respaldo = str(body.get('respaldo') or '')
    if not _RE_RESPALDO.match(respaldo):
        raise HTTPException(status_code=400, detail="Respaldo inválido")
    rc, _ = _git(cwd, 'rev-parse', '--verify', '--quiet', respaldo + '^{commit}')
    if rc != 0:
        raise HTTPException(status_code=400, detail="Ese respaldo ya no existe")
    rels = _rels_seguros(cwd, body.get('archivos'))
    # Solo lo que la foto tiene (un path ausente haría fallar el restore entero).
    rc, listado = _git(cwd, '-c', 'core.quotepath=false', 'ls-tree', '-r', '--name-only',
                       respaldo, '--', *rels)
    presentes = {l.strip() for l in listado.splitlines() if l.strip()}
    restaurar = [r for r in rels
                 if any(p == r.rstrip('/') or p.startswith(r.rstrip('/') + '/') for p in presentes)]
    if restaurar:
        rc, out, err = _git_full(cwd, 'restore', f'--source={respaldo}', '--worktree', '--', *restaurar)
        if rc != 0:
            return {"ok": False, "error": _error_git(out, err, "git restore falló")}
    return {"ok": True, "restaurados": restaurar}


def _rama_base(cwd: str) -> str:
    """Rama default del remoto (origin/HEAD); si no está seteado, main/master
    según exista. '' si no hay nada que lo indique."""
    rc, out = _git(cwd, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD')
    if rc == 0 and out.strip():
        return out.strip().split('/', 1)[-1]
    for cand in ('main', 'master'):
        for ref in (f'refs/remotes/origin/{cand}', f'refs/heads/{cand}'):
            rc, _ = _git(cwd, 'rev-parse', '--verify', '--quiet', ref)
            if rc == 0:
                return cand
    return ''


_URL_RE = re.compile(r'https://\S+/pull/\d+')


def _gh(cwd: str, *args: str) -> tuple[int, str, str]:
    """`gh` síncrono, igual contrato que _git_full. Sin gh instalado → rc -1."""
    try:
        r = subprocess.run(['gh', *args], cwd=cwd, capture_output=True, text=True,
                           timeout=GH_TIMEOUT)
        return r.returncode, (r.stdout or ''), (r.stderr or '')
    except FileNotFoundError:
        return -1, '', 'gh no está instalado (https://cli.github.com)'
    except subprocess.TimeoutExpired:
        return -1, '', 'gh timeout'
    except Exception as e:
        return -1, '', str(e)


@router.post("/projects/{project_id}/review/pr")
def abrir_pr(project_id: int, body: dict = Body(default=None)):
    """Sube la rama ACTUAL a origin y abre un PR contra la rama base con `gh`.
    Nunca cambia de rama ni toca el working tree (el árbol es compartido): solo
    entra lo ya commiteado. En la rama base se rechaza — un PR de master a
    master no existe; primero hay que trabajar en una rama."""
    body = body or {}
    titulo = str(body.get('titulo') or '').strip()
    cuerpo = str(body.get('cuerpo') or '').strip()
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")
    _, rama = _git(cwd, 'branch', '--show-current')
    rama = rama.strip()
    if not rama:
        raise HTTPException(status_code=400, detail="HEAD desacoplado: no hay rama para el PR")
    base = str(body.get('base') or '').strip() or _rama_base(cwd)
    if not base:
        raise HTTPException(status_code=400, detail="No se pudo determinar la rama base")
    if rama == base:
        raise HTTPException(status_code=400,
                            detail=f"Estás en la rama base ({base}): creá una rama para abrir un PR")
    rc, _ = _git(cwd, 'remote', 'get-url', 'origin')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no tiene remoto 'origin'")

    # El pre-push corre (sin --no-verify): el scanner de secretos sigue activo.
    rc, out, err = _git_full(cwd, 'push', '-u', 'origin', rama, timeout=GIT_COMMIT_TIMEOUT)
    if rc != 0:
        return {"ok": False, "error": _error_git(out, err, "git push falló")}

    args = ['pr', 'create', '--base', base, '--head', rama]
    if titulo:
        args += ['--title', titulo, '--body', cuerpo]
    else:
        args += ['--fill']
    rc, out, err = _gh(cwd, *args)
    url = _URL_RE.search(out) or _URL_RE.search(err)
    if rc != 0:
        # Ya había un PR para esta rama: gh lo dice con la URL — es un éxito.
        if url and 'already exists' in err:
            return {"ok": True, "url": url.group(0), "existente": True, "rama": rama, "base": base}
        return {"ok": False, "error": _error_git(out, err, "gh pr create falló")}
    return {"ok": True, "url": url.group(0) if url else out.strip(),
            "existente": False, "rama": rama, "base": base}


@router.get("/projects/{project_id}/review")
def estado_review(project_id: int):
    """Archivos cambiados + diff unificado del working tree contra HEAD
    (incluye untracked vía intent-to-add virtual con --no-index fallback)."""
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")

    # Lista de archivos con su estado
    _, porcelain = _git(cwd, 'status', '--porcelain')
    archivos = []
    untracked = []
    for linea in porcelain.splitlines():
        if len(linea) < 4:
            continue
        estado, path = linea[:2], linea[3:].strip()
        if estado == '??':
            untracked.append(path)
            archivos.append({'path': path, 'estado': 'A', 'untracked': True})
        else:
            letra = (estado.strip() or 'M')[0]
            archivos.append({'path': path, 'estado': letra, 'untracked': False})

    # Diff de tracked (working tree vs HEAD)
    _, diff = _git(cwd, 'diff', 'HEAD', '--no-color')

    # Untracked: mostrarlos como archivos nuevos completos
    for path in untracked[:40]:  # cap: no reventar con node_modules sueltos
        rc2, d2 = _git(cwd, 'diff', '--no-color', '--no-index', '/dev/null', path)
        if d2.strip():
            diff += '\n' + d2

    truncado = False
    if len(diff.encode()) > MAX_DIFF_BYTES:
        diff = diff.encode()[:MAX_DIFF_BYTES].decode(errors='ignore')
        diff += '\n\n… (diff truncado para la UI — revisalo completo en una terminal)'
        truncado = True

    # Stats por archivo (numstat: añadidas/borradas)
    stats = {}
    _, numstat = _git(cwd, 'diff', 'HEAD', '--numstat')
    for linea in numstat.splitlines():
        partes = linea.split('\t')
        if len(partes) == 3:
            mas, menos, path = partes
            stats[path] = {'mas': mas, 'menos': menos}
    # Untracked: numstat contra /dev/null — un archivo nuevo son N líneas
    # agregadas (antes quedaba '·' y el panel mostraba «+· −·»). Las carpetas
    # untracked ('dir/') no tienen conteo: se quedan sin números.
    for path in untracked[:40]:
        if path.endswith('/') or path in stats:
            continue
        _, ns = _git(cwd, 'diff', '--no-index', '--numstat', '/dev/null', path)
        partes = (ns.splitlines() or [''])[0].split('\t')
        if len(partes) == 3:
            stats[path] = {'mas': partes[0], 'menos': partes[1]}
    for a in archivos:
        s = stats.get(a['path'], {})
        a['mas']   = s.get('mas', '·')
        a['menos'] = s.get('menos', '·')
        a['clase'] = politica_commit.clasificar(a['path'])

    # Branch + último commit para contexto
    _, branch = _git(cwd, 'branch', '--show-current')
    _, ultimo = _git(cwd, 'log', '--oneline', '-1')

    return {
        'branch':   branch.strip(),
        'base':     _rama_base(cwd),
        'ultimo':   ultimo.strip(),
        'archivos': archivos,
        'diff':     diff,
        'truncado': truncado,
        'limpio':   not archivos,
    }


# ─── Editor: gutter de cambios + blame por línea ─────────────────────────────

def _parse_diff_hunks(diff: str) -> list:
    """Parsea `git diff --unified=0` y devuelve rangos del archivo NUEVO:
    [{'start','end','type'}] con type add|mod|del. Puro y testeable."""
    hunks = []
    cur = None

    def cerrar(h):
        start = int(h['start']); count = int(h['count'])
        if count <= 0:
            ln = max(1, start)          # borrado puro: marcar la línea donde cayó
            return {'start': ln, 'end': ln, 'type': 'del'}
        tipo = 'add' if (h['mas'] and not h['menos']) else 'mod'
        return {'start': start, 'end': start + count - 1, 'type': tipo}

    for ln in diff.split('\n'):
        if ln.startswith('@@'):
            if cur:
                hunks.append(cerrar(cur))
            m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', ln)
            cur = ({'start': int(m.group(1)), 'count': int(m.group(2) or 1),
                    'mas': 0, 'menos': 0} if m else None)
        elif cur is not None and ln[:1] in ('+', '-'):
            cur['mas' if ln[0] == '+' else 'menos'] += 1
    if cur:
        hunks.append(cerrar(cur))
    return hunks


def _parse_blame(out: str) -> dict:
    """Extrae autor/tiempo/resumen del `git blame --porcelain -L n,n`."""
    lineas = out.split('\n')
    if not lineas or not lineas[0]:
        return {}
    info = {'hash': lineas[0].split(' ')[0][:8], 'autor': '',
            'tiempo': 0, 'resumen': ''}
    for ln in lineas[1:]:
        if ln.startswith('author '):
            info['autor'] = ln[7:].strip()
        elif ln.startswith('author-time '):
            try:
                info['tiempo'] = int(ln[12:].strip())
            except ValueError:
                pass
        elif ln.startswith('summary '):
            info['resumen'] = ln[8:].strip()
        elif ln.startswith('\t'):
            break
    return info


@router.get("/projects/{project_id}/files/git-diff")
def git_diff_archivo(project_id: int, path: str = Query(...)):
    """Rangos de líneas cambiadas de UN archivo vs HEAD (gutter del editor)."""
    cwd = _ruta_proyecto(project_id)
    rel = _path_relativo_seguro(cwd, path)
    if rel is None:
        raise HTTPException(status_code=400, detail="Ruta fuera del proyecto")
    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        return {'git': False, 'hunks': []}

    rc_track, _ = _git(cwd, 'ls-files', '--error-unmatch', '--', rel)
    if rc_track != 0:   # untracked: el archivo entero es nuevo
        full = os.path.join(cwd, rel)
        if os.path.isfile(full):
            try:
                with open(full, 'r', encoding='utf-8', errors='replace') as f:
                    n = sum(1 for _ in f)
            except OSError:
                n = 0
            return {'git': True, 'hunks': [{'start': 1, 'end': max(1, n), 'type': 'add'}]}
        return {'git': True, 'hunks': []}

    _, diff = _git(cwd, '-c', 'core.quotepath=false',
                   'diff', '--unified=0', '--no-color', 'HEAD', '--', rel)
    return {'git': True, 'hunks': _parse_diff_hunks(diff)}


@router.get("/projects/{project_id}/files/git-blame")
def git_blame_linea(project_id: int, path: str = Query(...), line: int = Query(1)):
    """Blame de UNA línea (blame inline del editor)."""
    cwd = _ruta_proyecto(project_id)
    rel = _path_relativo_seguro(cwd, path)
    if rel is None:
        raise HTTPException(status_code=400, detail="Ruta fuera del proyecto")
    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        return {'git': False}
    ln = max(1, int(line))
    rc, out = _git(cwd, 'blame', '--porcelain', '-L', f'{ln},{ln}', '--', rel)
    if rc != 0:
        return {'git': False}
    return {'git': True, 'blame': _parse_blame(out)}


@router.get("/projects/{project_id}/review/file")
def review_file(project_id: int, path: str = Query(...)):
    """Par original (HEAD) vs modificado (working tree) de UN archivo, para el
    diff de Monaco del Review. `binary` → la UI cae al diff de texto."""
    cwd = _ruta_proyecto(project_id)
    rel = _path_relativo_seguro(cwd, path)
    if rel is None:
        raise HTTPException(status_code=400, detail="Ruta fuera del proyecto")
    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")

    rc, original = _git(cwd, 'show', f'HEAD:{rel}')
    if rc != 0:
        original = ''
    original = original.replace('\r\n', '\n')

    modified = ''
    binary = False
    full = os.path.join(cwd, rel)
    if os.path.isfile(full):
        try:
            with open(full, 'r', encoding='utf-8') as f:
                modified = f.read()
            modified = modified.replace('\r\n', '\n')
        except (UnicodeDecodeError, OSError):
            binary = True

    try:
        from plotspace.routers.projects_files import _detectar_lenguaje
        language = _detectar_lenguaje(rel)
    except Exception:
        language = 'plaintext'
    return {'original': original, 'modified': modified,
            'binary': binary, 'language': language}


# NOTA: el endpoint POST /review/aprobar (git add -A + commit) se quitó: hacía
# justo lo que el CLAUDE.md prohíbe ("NUNCA git add -A") y el hook pre-commit
# bloquea, así que estaba roto por diseño en el árbol compartido sobre main. La
# Review Room queda como VISOR de diff (GET de arriba); el commit lo hace cada
# agente con sus archivos explícitos.
