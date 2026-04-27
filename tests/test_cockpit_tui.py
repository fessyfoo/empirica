"""Tests for the empirica tui (compact Textual cockpit).

Uses textual's headless `App.run_test()`. Layout under test is the v1.4
compact design: 6-col table (stat/name/phase/S/L/N), 4 action buttons
(P sent / L loops / S stop / N notif), no kill button (CLI-only), no
right-detail pane, single statusline + recent strip at the bottom.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest_plugins = ['pytest_asyncio']


@pytest.fixture
def cockpit_env(tmp_path, monkeypatch):
    fake_home = tmp_path / '.empirica'
    fake_home.mkdir()
    (fake_home / 'instance_projects').mkdir()
    (fake_home / 'tty_sessions').mkdir()

    project = tmp_path / 'project'
    (project / '.empirica').mkdir(parents=True)

    from empirica.core.cockpit import (
        enrichment,
        instance_actions,
        instance_state,
        loop_registry,
        sentinel_pause,
    )

    monkeypatch.setattr(sentinel_pause, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(sentinel_pause, 'GLOBAL_PAUSE_FILE', fake_home / 'sentinel_paused')
    monkeypatch.setattr(instance_state, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(instance_actions, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(instance_actions, 'TTY_SESSIONS_DIR', fake_home / 'tty_sessions')
    monkeypatch.setattr(loop_registry, 'EMPIRICA_DIR', fake_home)
    monkeypatch.setattr(enrichment, 'EMPIRICA_DIR', fake_home)
    return fake_home, project


def _bind_instance(home: Path, project: Path, instance_id: str) -> None:
    (home / 'instance_projects' / f'{instance_id}.json').write_text(
        json.dumps({'project_path': str(project)})
    )


# ─── mount + structure ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tui_mounts_compact_widgets(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from textual.widgets import Button, DataTable, Static

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()

        assert app.query_one('#inst-table', DataTable) is not None
        assert app.query_one('#summary', Static) is not None
        assert app.query_one('#statusline', Static) is not None
        assert app.query_one('#recent', Static) is not None
        for btn_id in ('btn-sent', 'btn-loops', 'btn-stop', 'btn-notif'):
            assert app.query_one(f'#{btn_id}', Button) is not None, btn_id


@pytest.mark.asyncio
async def test_tui_has_no_kill_button(cockpit_env):
    """Kill is CLI-only by design; the TUI should not expose it."""
    from empirica.cli.tui import CockpitApp
    from textual.css.query import NoMatches

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        with pytest.raises(NoMatches):
            app.query_one('#btn-kill')


@pytest.mark.asyncio
async def test_table_has_six_columns(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from textual.widgets import DataTable

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        table = app.query_one('#inst-table', DataTable)
        col_labels = [c.label.plain for c in table.columns.values()]
        assert col_labels == ['stat', 'name', 'phase', 'S', 'L', 'N']


# ─── data loading ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tui_loads_instances(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    _bind_instance(home, project, 'tmux_99')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        ids = sorted(i['instance_id'] for i in app.payload['instances'])
        assert ids == ['tmux_42', 'tmux_99']


# ─── toggle sentinel ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p_toggles_sentinel(cockpit_env):
    """`p` toggles: pauses if not paused, resumes if paused."""
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()

        # First press: pauses
        await pilot.press('p')
        await pilot.pause()
        assert (home / 'sentinel_paused_tmux_42').exists()

        # Second press: resumes
        await pilot.press('p')
        await pilot.pause()
        assert not (home / 'sentinel_paused_tmux_42').exists()


@pytest.mark.asyncio
async def test_btn_sent_toggles_sentinel(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.click('#btn-sent')
        await pilot.pause()
        assert (home / 'sentinel_paused_tmux_42').exists()


# ─── toggle loops ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l_pauses_all_loops_when_any_unpaused(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import LoopRegistry

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    reg = LoopRegistry('tmux_42')
    reg.register(name='loop-a', kind='monitor')
    reg.register(name='loop-b', kind='monitor')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press('l')
        await pilot.pause()

        from empirica.core.cockpit import is_loop_paused
        assert is_loop_paused('tmux_42', 'loop-a')
        assert is_loop_paused('tmux_42', 'loop-b')


@pytest.mark.asyncio
async def test_l_resumes_all_loops_when_all_paused(cockpit_env):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import LoopRegistry, is_loop_paused, set_loop_paused

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    reg = LoopRegistry('tmux_42')
    reg.register(name='loop-a', kind='monitor')
    reg.register(name='loop-b', kind='monitor')
    set_loop_paused('tmux_42', 'loop-a', True)
    set_loop_paused('tmux_42', 'loop-b', True)

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press('l')
        await pilot.pause()

        assert not is_loop_paused('tmux_42', 'loop-a')
        assert not is_loop_paused('tmux_42', 'loop-b')


# ─── stop ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_calls_stop_instance(cockpit_env, monkeypatch):
    from empirica.cli.tui import CockpitApp
    from empirica.core.cockpit import instance_actions

    captured: list[str] = []
    monkeypatch.setattr(
        'empirica.cli.tui.cockpit_app.stop_instance',
        lambda iid, **k: (captured.append(iid),
                          instance_actions.StopResult(iid, True, 'stub', 'tmux-send-keys'))[1],
    )

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press('s')
        await pilot.pause()

        assert captured == ['tmux_42']


# ─── notifications (placeholder) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_n_clears_notifications(cockpit_env):
    from empirica.cli.tui import CockpitApp

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    enp_dir = home / 'enp'
    enp_dir.mkdir()
    (enp_dir / 'open_tmux_42.json').write_text(json.dumps({'open_count': 3}))

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press('n')
        await pilot.pause()

        assert not (enp_dir / 'open_tmux_42.json').exists()


# ─── ask state phase ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase_ask_when_asking_flag_present(cockpit_env):
    """Phase shows 'ask ⚠' when ~/.empirica/asking_{id} flag exists."""
    from empirica.cli.tui import CockpitApp
    from textual.widgets import DataTable

    home, project = cockpit_env
    _bind_instance(home, project, 'tmux_42')
    # Create transaction so phase isn't no-transaction
    import time
    suffix = '_tmux_42'
    tx = {
        'transaction_id': 'tx-1', 'session_id': 's-1',
        'preflight_timestamp': time.time(),
        'status': 'open', 'project_path': str(project),
    }
    (project / '.empirica' / f'active_transaction{suffix}.json').write_text(json.dumps(tx))
    # Trigger ask
    (home / 'asking_tmux_42').write_text('')

    app = CockpitApp(include_dead=True)
    async with app.run_test(headless=True, size=(54, 20)) as pilot:
        await pilot.pause(); await pilot.pause()
        table = app.query_one('#inst-table', DataTable)
        # Match row by key (instance_id) — label may be the project basename.
        for row_key in table.rows:
            if str(row_key.value) == 'tmux_42':
                row = table.get_row(row_key)
                phase_cell = str(row[2])
                assert 'ask' in phase_cell, f'expected ask phase, got {phase_cell!r}'
                return
        pytest.fail('tmux_42 row not found')
