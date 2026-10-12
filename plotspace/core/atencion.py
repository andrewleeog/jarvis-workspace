"""Atención entre proyectos: qué agentes te necesitan, en CUALQUIER proyecto.

Los avisos de agent_watch y del sentinel viajan por el WS del PROYECTO
(`/ws/events/{pid}`): mirando el proyecto A, que un agente de B quedara
esperando una respuesta, se bloqueara, fallara o se quedara sin cuota no llegaba
nunca a la pantalla. Este módulo escucha TODOS los eventos
(`broadcaster.escuchar`), lleva por proyecto qué terminales necesitan atención y:

  · lo anuncia GLOBAL al aparecer uno nuevo (`proyecto_atencion`) → el workspace
    que mira OTRO proyecto suena, muestra un toast con «Ir» y, si la pestaña
    está oculta, la notificación del SO;
  · lo expone por NIVEL (`pendientes()`, servido en GET /api/projects/working):
    la franja lo reconcilia cada 3 s, así un evento perdido se auto-repara.

Motivos: 'espera' (prompt a la vista) · 'bloqueado' (TASK_BLOCKED) · 'error'
(TASK_ERROR) · 'limite' (rate-limit sin otra cuenta a la que rotar).
Se limpia solo cuando se resuelve: el agente volvió a trabajar, terminó bien
(agente_termino / TASK_DONE) o su terminal se cerró (`olvidar_terminal`).
"""
import asyncio
import time

from plotspace.core.database import get_db

_MOTIVO_POR_EVENTO = {
    'agente_espera': 'espera',
    'limite_sin_cuenta': 'limite',
}
_MOTIVO_POR_TASK = {
    'TASK_BLOCKED': 'bloqueado',
    'TASK_ERROR': 'error',
}
_RESUELVEN = {'agente_trabajando', 'agente_termino', 'cuenta_rotada'}

# pid → tid → {'motivo', 'terminal_nombre', 'detalle', 'ts'}
_pend: dict = {}
_listener_registrado = False


def reset() -> None:
    _pend.clear()


def clasificar(data: dict):
    """(tid, motivo) para un evento del broadcaster: motivo str = necesita
    atención · None = se resolvió · ('', '') = no es de este módulo. Puro."""
    tipo = data.get('type')
    try:
        tid = int(data.get('terminal_id'))
    except (TypeError, ValueError):
        return None, ''
    if tipo in _MOTIVO_POR_EVENTO:
        return tid, _MOTIVO_POR_EVENTO[tipo]
    if tipo == 'task_event':
        ev = data.get('event')
        if ev in _MOTIVO_POR_TASK:
            return tid, _MOTIVO_POR_TASK[ev]
        if ev == 'TASK_DONE':
            return tid, None
        return None, ''
    if tipo in _RESUELVEN:
        return tid, None
    return None, ''


def _info_terminal(tid: int):
    """{'pid', 'nombre', 'activa'} de la terminal, o None si no existe."""
    conn = get_db()
    try:
        row = conn.execute('SELECT project_id, nombre, activa FROM terminals WHERE id = ?',
                           (tid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {'pid': row['project_id'], 'nombre': row['nombre'] or '', 'activa': bool(row['activa'])}


def _nombre_proyecto(pid: int) -> str:
    conn = get_db()
    try:
        row = conn.execute('SELECT nombre FROM projects WHERE id = ?', (pid,)).fetchone()
    finally:
        conn.close()
    return (row['nombre'] if row else '') or ''


def aplicar(data: dict, info: dict = None, ahora: float = None):
    """Aplica un evento al estado. Devuelve el aviso NUEVO a anunciar
    ({project_id, terminal_id, terminal_nombre, motivo, detalle}) o None.
    `info` = _info_terminal(tid) (inyectable en tests)."""
    tid, motivo = clasificar(data)
    if tid is None:
        return None
    if motivo is None:
        olvidar(tid)
        return None
    if not info or not info.get('activa'):
        return None
    pid = info['pid']
    nombre = data.get('terminal_nombre') or info.get('nombre') or f'Terminal {tid}'
    detalle = str(data.get('motivo') or data.get('tipo_ia') or '')[:200]
    previo = _pend.get(pid, {}).get(tid)
    _pend.setdefault(pid, {})[tid] = {
        'motivo': motivo, 'terminal_nombre': nombre, 'detalle': detalle,
        'ts': time.time() if ahora is None else ahora,
    }
    if previo and previo['motivo'] == motivo:
        return None          # el mismo aviso otra vez (agent_watch re-emite): sin re-anunciar
    return {'project_id': pid, 'terminal_id': tid, 'terminal_nombre': nombre,
            'motivo': motivo, 'detalle': detalle}


def olvidar(tid: int) -> bool:
    """Saca la terminal de los pendientes. True si había algo."""
    hubo = False
    for pid in list(_pend):
        if _pend[pid].pop(tid, None) is not None:
            hubo = True
        if not _pend[pid]:
            _pend.pop(pid, None)
    return hubo


def pendientes(activas=None) -> dict:
    """{str(pid): [{terminal_id, terminal_nombre, motivo, detalle, ts}]} — solo
    terminales que siguen activas (`activas`: set de tids; None = consultar la DB)."""
    if activas is None:
        try:
            conn = get_db()
            try:
                activas = {r['id'] for r in conn.execute('SELECT id FROM terminals WHERE activa = 1')}
            finally:
                conn.close()
        except Exception:
            activas = None
    out = {}
    for pid, por_tid in list(_pend.items()):
        filas = [dict(terminal_id=tid, **v) for tid, v in sorted(por_tid.items())
                 if activas is None or tid in activas]
        if filas:
            out[str(pid)] = filas
    return out


async def _on_evento(data: dict):
    tid, motivo = clasificar(data)
    if tid is None:
        return
    info = await asyncio.to_thread(_info_terminal, tid) if motivo else None
    nuevo = aplicar(data, info)
    if not nuevo:
        return
    from plotspace.core.events import broadcaster
    try:
        nuevo['project_nombre'] = await asyncio.to_thread(_nombre_proyecto, nuevo['project_id'])
    except Exception:
        nuevo['project_nombre'] = ''
    await broadcaster.broadcast_global({'type': 'proyecto_atencion', **nuevo})


def olvidar_terminal(tid: int) -> None:
    """La terminal se cerró: su aviso ya no tiene a quién llevarte."""
    olvidar(int(tid))


def registrar_listener() -> None:
    global _listener_registrado
    if _listener_registrado:
        return
    try:
        from plotspace.core.events import broadcaster
        broadcaster.escuchar(_on_evento)
        _listener_registrado = True
    except Exception as e:           # nunca romper el arranque por esto
        print(f'[atencion] no se pudo registrar el listener: {e}')
