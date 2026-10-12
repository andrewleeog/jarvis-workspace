"""La captura compartida cachea por TTL: dos pollers dentro de la ventana → 1 captura real."""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from plotspace.core import pane_capture as pc


@pytest.fixture(autouse=True)
def _sin_foto_de_actividad(monkeypatch):
    """Herméticos: sin esto, `_actividad` lee el tmux REAL de la máquina (si hay
    una sesión jarvis_7 viva, la captura se saltearía). None = «no se sabe»."""
    async def _nada(tid):
        return None
    monkeypatch.setattr(pc, '_actividad', _nada)


def test_cache_ttl_y_lock():
    async def main():
        pc._cache.clear(); pc._locks.clear()
        llamadas = {'n': 0}
        async def fake(tid):
            llamadas['n'] += 1
            await asyncio.sleep(0)   # cede el loop (simula el subprocess)
            return f'pane-{tid}-cap{llamadas["n"]}'
        pc._capturar_directo = fake

        # dos lecturas concurrentes dentro del TTL → UNA sola captura real (lock + cache)
        a, b = await asyncio.gather(pc.capturar(7, ttl=5.0), pc.capturar(7, ttl=5.0))
        assert a == b and llamadas['n'] == 1, (a, b, llamadas)

        # tercera lectura, mismo TTL → sigue cacheada (no captura de nuevo)
        c = await pc.capturar(7, ttl=5.0)
        assert c == a and llamadas['n'] == 1

        # con TTL 0 → fuerza re-captura
        d = await pc.capturar(7, ttl=0.0)
        assert llamadas['n'] == 2 and d != a

    asyncio.run(main())


def test_cap_anti_leak():
    async def main():
        pc._cache.clear(); pc._locks.clear()
        async def fake(tid): return f'x{tid}'
        pc._capturar_directo = fake
        for tid in range(300):
            await pc.capturar(tid, ttl=999.0)
        assert len(pc._cache) <= 256
    asyncio.run(main())


def test_ttl_default_sirve_a_los_pollers_de_2s():
    """TTL default 1.2s: agent_live/dev_detect/deck (ticks de 2s) reusan SIEMPRE
    la captura fresca que agent_watch (1s) acaba de hacer → cero forks extra."""
    assert pc._TTL == 1.2


def test_agent_watch_captura_fresco_cada_tick():
    """La máquina de estados de agent_watch compara pane contra pane ENTRE ticks:
    si su captura viniera del cache (TTL ≥ su intervalo), vería el MISMO texto
    tick por medio y el conteo de cambios consecutivos jamás llegaría a
    POLLS_PARA_TRABAJANDO. Su TTL propio debe quedar SIEMPRE debajo del tick."""
    from plotspace.core import agent_watch
    assert agent_watch.TTL_CAPTURA_PROPIA < agent_watch.INTERVALO_S
    assert agent_watch.TTL_CAPTURA_PROPIA < pc._TTL


def test_ttl_propio_fuerza_recaptura_y_alimenta_el_cache():
    """A la misma edad de cache, el ttl corto (agent_watch) recaptura y el default
    (pollers lentos) reusa lo recapturado."""
    async def main():
        pc._cache.clear(); pc._locks.clear()
        llamadas = {'n': 0}

        async def fake(tid):
            llamadas['n'] += 1
            return f'cap{llamadas["n"]}'
        orig = pc._capturar_directo
        pc._capturar_directo = fake
        try:
            a = await pc.capturar(7)                 # 1ª captura real
            # Envejecer la entrada 1.0s (edad típica al tick siguiente del poller)
            ts, texto, wall = pc._cache[7]
            pc._cache[7] = (ts - 1.0, texto, wall)
            b = await pc.capturar(7)                 # default 1.2 → cache
            assert b == a and llamadas['n'] == 1
            c = await pc.capturar(7, ttl=0.9)        # agent_watch → fresco
            assert c != a and llamadas['n'] == 2
            d = await pc.capturar(7)                 # poller lento → lo recién capturado
            assert d == c and llamadas['n'] == 2
        finally:
            pc._capturar_directo = orig
    asyncio.run(main())


def test_pantalla_quieta_no_se_recaptura(monkeypatch):
    """Sin salida en el pane desde la última captura (actividad < segundo de la
    captura) → se devuelve el texto cacheado aunque el TTL venció: cero forks
    por terminal ociosa. Con salida nueva → se captura."""
    async def main():
        pc._cache.clear(); pc._locks.clear()
        llamadas = {'n': 0}
        act = {'v': None}

        async def fake(tid):
            llamadas['n'] += 1
            return f'cap{llamadas["n"]}'

        async def actividad(tid):
            return act['v']
        monkeypatch.setattr(pc, '_capturar_directo', fake)
        monkeypatch.setattr(pc, '_actividad', actividad)

        a = await pc.capturar(7, ttl=0.0)
        assert llamadas['n'] == 1
        wall = pc._cache[7][2]
        act['v'] = int(wall) - 5                     # última salida ANTES de la captura
        b = await pc.capturar(7, ttl=0.0)
        assert b == a and llamadas['n'] == 1, 'pantalla quieta: no debía forkear'
        act['v'] = int(wall) + 3                     # salió algo después
        c = await pc.capturar(7, ttl=0.0)
        assert c != a and llamadas['n'] == 2
        act['v'] = None                              # el motor no sabe → captura siempre
        await pc.capturar(7, ttl=0.0)
        assert llamadas['n'] == 3
    asyncio.run(main())


def test_quieta_desde_es_conservador():
    # Resolución de 1 s: una salida en el MISMO segundo de la captura cuenta como cambio.
    assert pc.quieta_desde(99, 100.4) is True
    assert pc.quieta_desde(100, 100.4) is False
    assert pc.quieta_desde(101, 100.4) is False
    assert pc.quieta_desde(None, 100.4) is False


def test_captura_vacia_no_se_reusa(monkeypatch):
    # Una sesión que no respondió ('') se reintenta aunque la actividad diga quieta.
    async def main():
        pc._cache.clear(); pc._locks.clear()
        llamadas = {'n': 0}

        async def fake(tid):
            llamadas['n'] += 1
            return '' if llamadas['n'] == 1 else 'ok'

        async def quieta(tid):
            return 0
        monkeypatch.setattr(pc, '_capturar_directo', fake)
        monkeypatch.setattr(pc, '_actividad', quieta)
        assert await pc.capturar(3, ttl=0.0) == ''
        assert await pc.capturar(3, ttl=0.0) == 'ok'
    asyncio.run(main())


if __name__ == "__main__":
    test_cache_ttl_y_lock(); print("  OK ttl_y_lock")
    test_cap_anti_leak();     print("  OK cap_anti_leak")
    test_ttl_default_sirve_a_los_pollers_de_2s(); print("  OK ttl_default")
    test_agent_watch_captura_fresco_cada_tick(); print("  OK ttl_agent_watch")
    test_ttl_propio_fuerza_recaptura_y_alimenta_el_cache(); print("  OK ttl_recaptura")
    print("test_pane_capture: TODOS OK")
