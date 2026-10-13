"""_env_terminal: el entorno con el que nacen TODAS las terminales de Jarvis."""
import asyncio
from unittest import mock

import pytest

import plotspace.core.terminal_backend as _tb
import plotspace.routers.terminals as term
from plotspace.routers.terminals import _env_sesion, _env_terminal, _VARS_FUERA_DEL_PANE


def test_sin_api_key_y_con_colores(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-' + 'falsa')  # concatenada: no dispara el escáner
    env = _env_terminal()
    assert 'ANTHROPIC_API_KEY' not in env
    assert env['TERM'] == 'xterm-256color'
    assert env['COLORTERM'] == 'truecolor'


def test_prompt_suggestions_apagadas(monkeypatch):
    """El 'prompt sugerido' (ghost text) de Claude Code v2.1.x agranda el cuadro
    de input con texto que el usuario no puso (pedido 2026-07-02: quitarlo):
    toda terminal de Jarvis nace con la feature APAGADA. Nombre de la var
    verificado contra el binario 2.1.198 instalado y la doc oficial
    (code.claude.com/docs/en/interactive-mode)."""
    monkeypatch.delenv('JARVIS_PROMPT_SUGGESTIONS', raising=False)
    env = _env_terminal()
    assert env['CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION'] == 'false'


def test_prompt_suggestions_escape_hatch(monkeypatch):
    """JARVIS_PROMPT_SUGGESTIONS=on restaura el default de Claude Code."""
    monkeypatch.setenv('JARVIS_PROMPT_SUGGESTIONS', 'on')
    monkeypatch.delenv('CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION', raising=False)
    env = _env_terminal()
    assert 'CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION' not in env


def test_claude_fullscreen_por_default(monkeypatch):
    """Claude Code nace en FULLSCREEN (CLAUDE_CODE_NO_FLICKER=1, v2.1.89+):
    el transcript vive contenido en alt-screen y NADA cae al scrollback — mata
    la familia 'texto multiplicado/cortado al maximizar' (video del usuario
    2026-07-02) y los residuos de /usage. Reintroducido de 3661136 (la 'bala
    de plata' que cayó en el revert masivo) con el OK explícito del usuario."""
    monkeypatch.delenv('JARVIS_CLAUDE_FULLSCREEN', raising=False)
    env = _env_terminal()
    assert env['CLAUDE_CODE_NO_FLICKER'] == '1'


def test_claude_fullscreen_escape_hatch(monkeypatch):
    """JARVIS_CLAUDE_FULLSCREEN=off vuelve al default de Claude Code."""
    monkeypatch.setenv('JARVIS_CLAUDE_FULLSCREEN', 'off')
    monkeypatch.delenv('CLAUDE_CODE_NO_FLICKER', raising=False)
    env = _env_terminal()
    assert 'CLAUDE_CODE_NO_FLICKER' not in env


def test_claude_full_repaint_por_default(monkeypatch):
    """CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT=1 acompaña al fullscreen (deep work
    2026-07-08, video del freeze): al scrollear una conversación GIGANTE
    (212.7k tokens) claude re-layoutea el transcript emitiendo frames PARCIALES
    (mueve bloques sin borrar el resto → regiones negras huérfanas) y se calla
    hasta 29.8s medidos. Con full-repaint cada frame borra y repinta el
    viewport completo (EL en todas las filas): regiones negras imposibles en
    origen y el peor silencio cae a 2.3s (-92%), medido con la MISMA
    conversación del incidente en claude v2.1.204. Costo: ~4x bytes solo
    durante scroll agresivo (~190KB/s pico) — trivial para el motor control."""
    monkeypatch.delenv('JARVIS_CLAUDE_FULLSCREEN', raising=False)
    monkeypatch.delenv('JARVIS_CLAUDE_FULL_REPAINT', raising=False)
    env = _env_terminal()
    assert env['CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT'] == '1'


def test_claude_full_repaint_escape_hatch(monkeypatch):
    """JARVIS_CLAUDE_FULL_REPAINT=off vuelve al render incremental de claude
    (menos bytes) sin apagar el fullscreen."""
    monkeypatch.delenv('JARVIS_CLAUDE_FULLSCREEN', raising=False)
    monkeypatch.setenv('JARVIS_CLAUDE_FULL_REPAINT', 'off')
    monkeypatch.delenv('CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT', raising=False)
    env = _env_terminal()
    assert env['CLAUDE_CODE_NO_FLICKER'] == '1'
    assert 'CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT' not in env


_MARCAS_SESION = (
    'CLAUDECODE', 'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_ENTRYPOINT',
    'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SSE_PORT',
    'CLAUDE_PID', 'CLAUDE_EFFORT',
)


def test_sin_marcas_de_la_sesion_claude_que_arranco_el_server(monkeypatch):
    """Si el server lo arrancó una sesión de Claude Code (un dev, un agente),
    su entorno viaja uvicorn → motor → terminal: los claude del workspace
    nacían creyéndose SESIÓN HIJA de esa otra ("Transcript saving is off —
    inherited CLAUDE_CODE_CHILD_SESSION marker", visto 2026-08-02 en la app
    de Linux). Las marcas de identidad de sesión se sellan acá; los knobs de
    config (NO_FLICKER, prompt suggestions) se inyectan DESPUÉS y no se tocan."""
    for var in _MARCAS_SESION:
        monkeypatch.setenv(var, 'x')
    env = _env_terminal()
    for var in _MARCAS_SESION:
        assert var not in env, var
    # Los knobs de config sobreviven al sello (se setean después de limpiar).
    assert env['CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION'] == 'false'


def test_claude_full_repaint_sigue_al_fullscreen(monkeypatch):
    """Sin fullscreen (render inline clásico) el full-repaint no aplica: la
    var no se inyecta para no cambiar el comportamiento del modo default."""
    monkeypatch.setenv('JARVIS_CLAUDE_FULLSCREEN', 'off')
    monkeypatch.delenv('JARVIS_CLAUDE_FULL_REPAINT', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT', raising=False)
    env = _env_terminal()
    assert 'CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT' not in env


# Lo que apaga el color de las CLIs: con cualquiera de estas en el entorno
# heredado, Claude Code se pinta gris (medido 2026-10-13: 55 → 17 secuencias
# de color con NO_COLOR, FORCE_COLOR=0, CI o TERM=dumb).
_APAGAN_COLOR = ('NO_COLOR', 'FORCE_COLOR', 'NODE_DISABLE_COLORS', 'CI')


def test_sin_vars_que_apagan_el_color(monkeypatch):
    for var in _APAGAN_COLOR:
        monkeypatch.setenv(var, '1')
    env = _env_terminal()
    for var in _APAGAN_COLOR:
        assert var not in env, var
    assert env['COLORTERM'] == 'truecolor'


def test_vars_fuera_del_pane_cubre_marcas_y_color():
    for var in (*_MARCAS_SESION, *_APAGAN_COLOR, 'ANTHROPIC_API_KEY'):
        assert var in _VARS_FUERA_DEL_PANE, var


def test_env_sesion_lleva_color_y_ningun_secreto(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-' + 'falsa')
    env = _env_sesion()
    assert env['COLORTERM'] == 'truecolor'
    assert not set(env) & set(_VARS_FUERA_DEL_PANE)


def _new_session_con_global(stdout_global):
    """argv del new-session de `_crear_sesion_tmux` con un server tmux cuyo
    entorno global es `stdout_global` (salida de `show-environment -g`)."""
    def run(argv, **kw):
        out = stdout_global if argv[:2] == ['tmux', 'show-environment'] else ''
        if argv[:3] == ['tmux', 'show-options', '-gv']:
            out = '/bin/bash\n'
        return mock.Mock(returncode=0, stderr='', stdout=out)
    with mock.patch.object(term, '_sesion_tmux_existe', return_value=False), \
         mock.patch.object(_tb.TmuxBackend, 'existe', return_value=False), \
         mock.patch.object(term.os.path, 'isdir', return_value=True), \
         mock.patch.object(term, '_instalar_bindings_copy_mode'), \
         mock.patch.object(term, '_aplicar_estilo_obsidian_tmux'), \
         mock.patch.object(term, '_launch_command_de_terminal', return_value=None), \
         mock.patch.object(term.subprocess, 'run', side_effect=run) as m:
        asyncio.run(term._crear_sesion_tmux(999, '/tmp/x'))
    for c in m.call_args_list:
        argv = list(c.args[0])
        if argv[:2] == ['tmux', 'new-session']:
            return argv
    raise AssertionError('no hubo new-session')


@pytest.mark.usefixtures('motor_tmux')
def test_crear_sesion_fija_el_color_con_menos_e():
    """Con el server tmux ya vivo, el entorno del proceso no llega al pane:
    COLORTERM tiene que ir como `-e` de la sesión o Claude Code nace sin él."""
    ns = _new_session_con_global('PATH=/usr/bin\n')
    assert ns[ns.index('COLORTERM=truecolor') - 1] == '-e', ns
    # Sin nada que quitar, el pane es el shell de siempre: no hay comando al final.
    assert 'env' not in ns and ns[-2] == '-e', ns


@pytest.mark.usefixtures('motor_tmux')
def test_crear_sesion_quita_lo_heredado_del_server_tmux():
    """El server tmux lo arrancó otra sesión de Claude Code con NO_COLOR: el
    pane nace sin esas vars (`env -u`), no gris ni "sesión hija"."""
    ns = _new_session_con_global('PATH=/usr/bin\nCLAUDE_CODE_CHILD_SESSION=1\nNO_COLOR=1\n')
    assert ns[-7:] == ['env', '-u', 'CLAUDE_CODE_CHILD_SESSION', '-u', 'NO_COLOR',
                       '/bin/bash', '-l'], ns
