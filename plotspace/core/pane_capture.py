"""Captura COMPARTIDA de la pantalla de cada terminal, con cache por TTL.

Tres pollers (agent_watch ~1s, agent_live ~2s, dev_detect ~2s) capturaban CADA UNO la misma pantalla
por terminal por tick → ~3N capturas (fork+exec+pipe+decode) por segundo, redundantes.
Acá se captura UNA sola vez las 120 líneas (el máximo que pide cualquier consumidor) por terminal y se
sirve a los tres dentro de una ventana TTL corta; cada poller toma después la cola de líneas que necesita
(comportamiento idéntico al anterior). Menos overhead en el event loop → la detección/los sonidos llegan
más rápido. La captura del MONITOR DE KEYWORDS (TASK_DONE, terminals.py) es APARTE y NO pasa por acá →
la detección de cierres NO se toca.

De DÓNDE sale la pantalla lo decide el motor (`core/terminal_backend`):
`tmux capture-pane`. Este módulo solo se ocupa de no pedirla tres veces.

Y de no pedirla si NO CAMBIÓ: el motor sabe el último segundo con salida de
cada pane (`actividad_panes`, UNA foto `list-panes` para todas). Una pantalla
quieta desde la última captura devuelve el texto cacheado → con N terminales
ociosas el poller de 1s pasó de N forks/s a uno solo (la foto). Si el motor no
lo sabe (None), se captura como siempre.
"""
import asyncio
import time

from plotspace.core.terminal_backend import backend as motor_terminales

LINEAS_PANE = 120          # el máximo que pide cualquier consumidor (agent_live)
_TTL = 1.2                 # ventana de cache DEFAULT (s), para los pollers de 2s
                           # (agent_live/dev_detect): con 1.2 > 1s reusan SIEMPRE
                           # la captura fresca que agent_watch acaba de hacer (antes,
                           # con 0.8 < tick, el miss era frecuente → forks extra).
                           # agent_watch pide ttl=TTL_CAPTURA_PROPIA (<1s) explícito:
                           # su máquina de estados necesita captura fresca POR tick.

_TTL_FOTO = 0.9            # edad máx. de la foto de actividad (< el tick de 1s de agent_watch)

_cache: dict = {}          # terminal_id → (timestamp_monotonic, texto, wall_de_la_captura)
_locks: dict = {}          # terminal_id → asyncio.Lock (evita capturas duplicadas concurrentes)


async def _capturar_directo(terminal_id: int) -> str:
    """La pantalla cruda, sin cache — la sirve el motor de terminales."""
    return await motor_terminales().capturar_async(terminal_id, LINEAS_PANE)


async def _actividad(terminal_id: int):
    """Último segundo (epoch) con salida en el pane, o None si no se sabe."""
    motor = motor_terminales()
    try:
        foto = await asyncio.to_thread(motor.actividad_panes, _TTL_FOTO)
    except Exception:
        return None
    if not foto:
        return None
    return foto.get(motor.nombre_sesion(terminal_id))


def quieta_desde(actividad, wall_captura: float) -> bool:
    """¿La pantalla NO tuvo salida desde la captura? `actividad` tiene resolución
    de segundos: una salida en el mismo segundo de la captura cuenta como cambio
    (una re-captura de más, nunca una de menos)."""
    return actividad is not None and actividad < int(wall_captura)


async def capturar(terminal_id: int, ttl: float = _TTL) -> str:
    """Pane cacheado. Reusa una captura de < ttl segundos; si no, captura (con lock por terminal
    para que dos pollers concurrentes no disparen dos capturas a la vez)."""
    ent = _cache.get(terminal_id)
    if ent and (time.monotonic() - ent[0]) < ttl:
        return ent[1]
    lock = _locks.get(terminal_id)
    if lock is None:
        lock = _locks[terminal_id] = asyncio.Lock()
    async with lock:
        # Re-chequear DENTRO del lock: otro poller pudo capturar mientras esperábamos el lock.
        ent = _cache.get(terminal_id)
        if ent and (time.monotonic() - ent[0]) < ttl:
            return ent[1]
        # Sin salida desde la última captura → la pantalla es la misma: no forkear.
        if ent and texto_vigente(ent) and quieta_desde(await _actividad(terminal_id), ent[2]):
            _cache[terminal_id] = (time.monotonic(), ent[1], ent[2])
            return ent[1]
        wall = time.time()          # ANTES de capturar: lo que salga durante la captura cuenta
        texto = await _capturar_directo(terminal_id)
        _cache[terminal_id] = (time.monotonic(), texto, wall)
        if len(_cache) > 256:
            # Bound anti-leak: soltar las entradas más viejas (terminales muertas que nadie purgó).
            for tid in sorted(_cache, key=lambda k: _cache[k][0])[:len(_cache) - 256]:
                _cache.pop(tid, None)
                _locks.pop(tid, None)
        return texto


def texto_vigente(ent) -> bool:
    """Una captura vacía (sesión que no respondió) no se reusa: se reintenta."""
    return bool(ent and len(ent) > 2 and ent[1])
