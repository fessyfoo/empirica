"""Liveness detection for cockpit instances.

An instance is "alive" when there is reason to believe an actual Claude
Code process is running in it. Otherwise the cockpit shouldn't list it
by default — `kill` would just say "already dead", and the row is noise.

Signal hierarchy (first definitive signal wins):

  1. tmux instance: `tmux list-panes -a` includes %N → maybe alive (continue);
     %N missing → DEAD (terminal closed, Claude is gone with it).
  2. Captured PPID alive: os.kill(ppid, 0) succeeds → ALIVE.
     PPID dead → DEAD (Claude process exited).
  3. No PID and no tmux info, but recent activity (< RECENT_ACTIVITY_S):
     → ALIVE (likely fresh session that hasn't synced yet).
  4. Otherwise → DEAD.

A consequence: a tmux pane that exists but contains a plain shell where
Claude exited will show DEAD if we have a captured PPID — exactly the
case David flagged.

The tmux pane query is cached per-call to avoid spawning a subprocess per
instance during a status sweep.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EMPIRICA_DIR = Path.home() / '.empirica'
TTY_SESSIONS_DIR = EMPIRICA_DIR / 'tty_sessions'

TMUX_INSTANCE_PATTERN = re.compile(r'^tmux_(.+)$')

# An instance with no PID/tmux info but activity within this window is
# treated as alive (covers fresh sessions where session-init hasn't yet
# captured a PID).
RECENT_ACTIVITY_S = 60 * 60  # 1 hour


@dataclass
class LivenessResult:
    alive: bool
    reason: str
    pid_checked: int | None = None
    tmux_pane: str | None = None


# Commands tmux reports as the foreground process when Claude Code is running.
# 'claude' is the bin name; 'node' covers older installations / dev launches.
_CLAUDE_COMMANDS = frozenset({'claude', 'node'})


def _live_tmux_panes() -> set[str] | None:
    """Return set of pane numbers (e.g. {'1', '2', '3'}) where Claude Code is running.

    Uses `pane_current_command` to distinguish "Claude is running here" from
    "this pane exists but it's just a bash shell". A bash pane that once
    hosted Claude after the user `exit`ed is correctly classified as not
    hosting Claude — which is exactly what David flagged.

    Returns None if we couldn't query tmux at all (signal inconclusive,
    fall through to PID/activity checks).
    """
    if shutil.which('tmux') is None:
        return None
    try:
        result = subprocess.run(
            ['tmux', 'list-panes', '-a', '-F', '#{pane_id} #{pane_current_command}'],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        # tmux not running, no server, etc. — no Claude panes alive.
        return set()
    panes = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pane_id, cmd = parts
        if cmd in _CLAUDE_COMMANDS:
            panes.add(pane_id.lstrip('%'))
    return panes


def _all_tmux_panes() -> set[str] | None:
    """Return set of ALL pane numbers regardless of command. Used for
    distinguishing 'pane gone' (terminal closed) from 'pane exists but
    Claude exited' — both are 'dead' for the cockpit, but the explanation
    differs."""
    if shutil.which('tmux') is None:
        return None
    try:
        result = subprocess.run(
            ['tmux', 'list-panes', '-a', '-F', '#{pane_id}'],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return set()
    return {line.strip().lstrip('%') for line in result.stdout.splitlines() if line.strip()}


def _process_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read_captured_pids(instance_id: str) -> tuple[int | None, int | None]:
    """Return (pid, ppid) captured at session-init time, or (None, None)."""
    inst_file = EMPIRICA_DIR / 'instance_projects' / f'{instance_id}.json'
    if inst_file.exists():
        try:
            with open(inst_file, encoding='utf-8') as f:
                data = json.load(f)
            pid = data.get('pid') if isinstance(data.get('pid'), int) else None
            ppid = data.get('ppid') if isinstance(data.get('ppid'), int) else None
            if pid or ppid:
                return pid, ppid
            tty_key = data.get('tty_key')
        except (OSError, json.JSONDecodeError):
            tty_key = None
    else:
        tty_key = None

    if tty_key:
        tty_file = TTY_SESSIONS_DIR / f'{tty_key}.json'
        if tty_file.exists():
            try:
                with open(tty_file, encoding='utf-8') as f:
                    data = json.load(f)
                pid = data.get('pid') if isinstance(data.get('pid'), int) else None
                ppid = data.get('ppid') if isinstance(data.get('ppid'), int) else None
                return pid, ppid
            except (OSError, json.JSONDecodeError):
                pass

    return None, None


def is_alive(
    instance_id: str,
    last_activity_seconds: float | None = None,
    live_panes: set[str] | None = None,
    current_instance_id: str | None = None,
) -> LivenessResult:
    """Determine whether an instance is alive.

    Args:
        instance_id: the instance to check
        last_activity_seconds: seconds since most recent state-file write
        live_panes: pre-computed set of live tmux pane numbers (sweep
            optimization — pass None to query lazily)
        current_instance_id: if equal to instance_id, treat as alive
            (the running cockpit is alive by definition)
    """
    if current_instance_id and instance_id == current_instance_id:
        return LivenessResult(alive=True, reason='current instance')

    # Tmux pane check — definitive when tmux is queryable.
    # `live_panes` here is the set of panes where Claude is the foreground
    # process. A pane that exists with bash running == Claude exited.
    m = TMUX_INSTANCE_PATTERN.match(instance_id)
    if m:
        pane_n = m.group(1)
        if live_panes is None:
            live_panes = _live_tmux_panes()
        if live_panes is not None:
            if pane_n in live_panes:
                return LivenessResult(
                    alive=True,
                    reason=f'tmux pane %{pane_n} running claude',
                    tmux_pane=pane_n,
                )
            # Pane absent or running something other than Claude.
            all_panes = _all_tmux_panes() or set()
            if pane_n in all_panes:
                return LivenessResult(
                    alive=False,
                    reason=f'tmux pane %{pane_n} exists but claude is not running there',
                    tmux_pane=pane_n,
                )
            return LivenessResult(
                alive=False, reason=f'tmux pane %{pane_n} does not exist',
                tmux_pane=pane_n,
            )

    # PID check — definitive when we have one.
    pid, ppid = _read_captured_pids(instance_id)
    target_pid = ppid if ppid else pid
    if target_pid:
        if _process_alive(target_pid):
            return LivenessResult(
                alive=True, reason=f'pid {target_pid} alive', pid_checked=target_pid,
            )
        return LivenessResult(
            alive=False, reason=f'pid {target_pid} dead', pid_checked=target_pid,
        )

    # No PID — fall back to recent-activity window.
    if last_activity_seconds is not None and last_activity_seconds < RECENT_ACTIVITY_S:
        return LivenessResult(
            alive=True, reason=f'recent activity ({int(last_activity_seconds)}s ago)',
        )

    return LivenessResult(
        alive=False,
        reason='no pid, no recent activity, no tmux pane evidence',
    )


__all__ = ['LivenessResult', 'is_alive']
