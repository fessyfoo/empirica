"""Loop registry — per-instance JSON registry for `empirica loop` subcommand.

Replaces ad-hoc /tmp/{name}.disabled sentinel files with a uniform pattern:

  ~/.empirica/loops_{instance_id}.json   — declarative registry of loops
  ~/.empirica/loop_paused_{instance_id}_{name}  — empty file == paused

`register` is idempotent on (instance_id, name). `heartbeat` mutates the
last_run/last_status/last_message fields. Pause/resume operate on the
sidecar file so they're trivially atomic against the registry write.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'
VALID_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
VALID_KIND = ('cron', 'interval', 'monitor')
VALID_STATUS = ('ok', 'fail')
VALID_BACKOFF = ('none', 'exponential')
VALID_RESULT = ('found', 'empty', 'fail')

# Default backoff envelope when caller passes --backoff exponential without
# explicit floor/ceiling. 15m base × 2^N capped at 4h matches the proposal.
DEFAULT_BASE_INTERVAL_S = 15 * 60
DEFAULT_MAX_INTERVAL_S = 4 * 60 * 60

_DURATION_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([smhd])$', re.IGNORECASE)


def parse_duration(text: str | None) -> int | None:
    """Parse '15m', '4h', '30s', '1d' to seconds. Bare integers → minutes."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).strip().lower()
    if not s:
        return None
    m = _DURATION_RE.match(s)
    if m:
        value = float(m.group(1))
        unit = m.group(2)
        return int(value * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit])
    try:
        return int(float(s) * 60)  # bare number → minutes
    except ValueError:
        return None


def format_duration(seconds: int | None) -> str:
    """Reverse of parse_duration — pick the largest unit that's whole-ish."""
    if seconds is None:
        return ''
    if seconds <= 0:
        return '0s'
    for unit_s, unit in ((86400, 'd'), (3600, 'h'), (60, 'm')):
        if seconds % unit_s == 0:
            return f'{seconds // unit_s}{unit}'
    return f'{seconds}s'


def _safe_suffix(text: str) -> str:
    return text.replace('/', '-').replace('%', '')


def registry_path(instance_id: str) -> Path:
    return EMPIRICA_DIR / f'loops_{_safe_suffix(instance_id)}.json'


def loop_pause_path(instance_id: str, name: str) -> Path:
    return EMPIRICA_DIR / f'loop_paused_{_safe_suffix(instance_id)}_{_safe_suffix(name)}'


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _validate_name(name: str) -> None:
    if not VALID_NAME.match(name):
        raise ValueError(
            f"Invalid loop name '{name}' — must match {VALID_NAME.pattern}"
        )


def _validate_kind(kind: str) -> None:
    if kind not in VALID_KIND:
        raise ValueError(f"Invalid kind '{kind}' — must be one of {VALID_KIND}")


def is_loop_paused(instance_id: str, name: str) -> bool:
    return loop_pause_path(instance_id, name).exists()


def set_loop_paused(instance_id: str, name: str, paused: bool) -> bool:
    """Set or clear the pause sidecar file for a loop. Returns paused state."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = loop_pause_path(instance_id, name)
    if paused:
        path.write_text('', encoding='utf-8')
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return path.exists()


@dataclass
class BackoffState:
    """Per-loop exponential-backoff bookkeeping (PROPOSAL_LOOP_BACKOFF.md).

    `policy='none'` is the legacy / default behavior — should_fire() always
    returns True regardless of streak. `policy='exponential'` uses
    base_interval_seconds * 2^empty_streak, capped at max_interval_seconds.

    next_fire_threshold is the wall-clock ISO time after which the loop
    body is allowed to do work. Empty fires advance it; found/fail fires
    snap it back to base.
    """
    policy: str = 'none'  # 'none' | 'exponential'
    base_interval_seconds: int | None = None
    max_interval_seconds: int | None = None
    empty_streak: int = 0
    next_fire_threshold: str | None = None  # ISO-8601 UTC, or None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            'policy': self.policy,
            'base_interval_seconds': self.base_interval_seconds,
            'max_interval_seconds': self.max_interval_seconds,
            'empty_streak': self.empty_streak,
            'next_fire_threshold': self.next_fire_threshold,
        }
        # Surface human-readable duration strings for renderers/JSON consumers.
        if self.base_interval_seconds:
            d['base_interval'] = format_duration(self.base_interval_seconds)
        if self.max_interval_seconds:
            d['max_interval'] = format_duration(self.max_interval_seconds)
        d['current_interval'] = format_duration(self.current_interval_seconds())
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BackoffState:
        if not data:
            return cls()
        return cls(
            policy=data.get('policy', 'none') or 'none',
            base_interval_seconds=_safe_int(data.get('base_interval_seconds')),
            max_interval_seconds=_safe_int(data.get('max_interval_seconds')),
            empty_streak=int(data.get('empty_streak', 0) or 0),
            next_fire_threshold=data.get('next_fire_threshold'),
        )

    def current_interval_seconds(self) -> int | None:
        """Compute base × 2^streak, capped at max. None when policy=none."""
        if self.policy != 'exponential' or not self.base_interval_seconds:
            return None
        # Avoid 2**100 — clamp the exponent at 16 (covers any sane envelope).
        exp = min(self.empty_streak, 16)
        candidate = self.base_interval_seconds * (2 ** exp)
        if self.max_interval_seconds:
            candidate = min(candidate, self.max_interval_seconds)
        return candidate

    def is_at_base(self) -> bool:
        if self.policy != 'exponential':
            return True
        return self.empty_streak == 0


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class LoopEntry:
    name: str
    kind: str  # 'cron' | 'interval' | 'monitor'
    cron: str | None = None
    interval: str | None = None  # e.g. "5m"
    description: str = ''
    registered_at: str = field(default_factory=_now_iso)
    last_run: str | None = None
    last_status: str | None = None  # 'ok' | 'fail'
    last_message: str | None = None
    last_result: str | None = None  # 'found' | 'empty' | 'fail' (PROPOSAL_LOOP_BACKOFF)
    backoff: BackoffState = field(default_factory=BackoffState)

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'cron': self.cron,
            'interval': self.interval,
            'description': self.description,
            'registered_at': self.registered_at,
            'last_run': self.last_run,
            'last_status': self.last_status,
            'last_message': self.last_message,
            'last_result': self.last_result,
            'backoff': self.backoff.to_dict(),
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> LoopEntry:
        return cls(
            name=name,
            kind=data.get('kind', 'monitor'),
            cron=data.get('cron'),
            interval=data.get('interval'),
            description=data.get('description', ''),
            registered_at=data.get('registered_at', _now_iso()),
            last_run=data.get('last_run'),
            last_status=data.get('last_status'),
            last_message=data.get('last_message'),
            last_result=data.get('last_result'),
            backoff=BackoffState.from_dict(data.get('backoff')),
        )


class LoopRegistry:
    """Per-instance loop registry stored at ~/.empirica/loops_{instance_id}.json.

    All mutating methods read-modify-write the JSON file atomically (tempfile
    + rename). Pause state is a sidecar file, not a registry field — that
    keeps pause toggles independent of registry rewrites and lets the loop
    runner check pause without parsing the registry.
    """

    def __init__(self, instance_id: str, label: str | None = None):
        if not instance_id:
            raise ValueError("instance_id required")
        self.instance_id = instance_id
        self.path = registry_path(instance_id)
        self._label = label

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                'instance_id': self.instance_id,
                'instance_label': self._label,
                'loops': {},
            }
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            data.setdefault('instance_id', self.instance_id)
            data.setdefault('instance_label', self._label)
            data.setdefault('loops', {})
            return data
        except (OSError, json.JSONDecodeError):
            return {
                'instance_id': self.instance_id,
                'instance_label': self._label,
                'loops': {},
            }

    def _write(self, data: dict[str, Any]) -> None:
        EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
        # Atomic write: tempfile in same dir, fsync, rename
        fd, tmp_path = tempfile.mkstemp(
            prefix=self.path.name + '.', suffix='.tmp', dir=str(EMPIRICA_DIR)
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_loops(self) -> list[LoopEntry]:
        data = self._read()
        return [LoopEntry.from_dict(name, entry) for name, entry in data['loops'].items()]

    def get(self, name: str) -> LoopEntry | None:
        data = self._read()
        entry = data['loops'].get(name)
        if entry is None:
            return None
        return LoopEntry.from_dict(name, entry)

    def register(
        self,
        name: str,
        kind: str,
        cron: str | None = None,
        interval: str | None = None,
        description: str = '',
        backoff_policy: str | None = None,
        base_interval: str | None = None,
        max_interval: str | None = None,
    ) -> LoopEntry:
        """Register a loop. Idempotent on (instance_id, name).

        Re-registering an existing loop preserves runtime state (last_run,
        last_status, last_message, last_result, empty_streak,
        next_fire_threshold) but updates declarative fields including the
        backoff envelope. Lets callers safely re-issue `register` at
        startup without losing history.

        Backoff:
          backoff_policy='none' (default) → no backoff
          backoff_policy='exponential'    → empty fires advance threshold
            base_interval defaults to 15m, max_interval to 4h.
        """
        _validate_name(name)
        _validate_kind(kind)
        if backoff_policy is not None and backoff_policy not in VALID_BACKOFF:
            raise ValueError(
                f"Invalid backoff '{backoff_policy}' — must be one of {VALID_BACKOFF}"
            )

        data = self._read()
        existing = data['loops'].get(name)

        if existing:
            entry = LoopEntry.from_dict(name, existing)
            entry.kind = kind
            entry.cron = cron
            entry.interval = interval
            entry.description = description
        else:
            entry = LoopEntry(
                name=name,
                kind=kind,
                cron=cron,
                interval=interval,
                description=description,
            )

        # Backoff envelope: only mutate if caller specified a policy.
        if backoff_policy is not None:
            entry.backoff.policy = backoff_policy
            if backoff_policy == 'exponential':
                base_s = parse_duration(base_interval) or DEFAULT_BASE_INTERVAL_S
                max_s = parse_duration(max_interval) or DEFAULT_MAX_INTERVAL_S
                if max_s < base_s:
                    raise ValueError(
                        f'max_interval ({max_s}s) must be >= base_interval ({base_s}s)'
                    )
                entry.backoff.base_interval_seconds = base_s
                entry.backoff.max_interval_seconds = max_s
            else:
                # Switching to 'none' clears the envelope and any pending threshold.
                entry.backoff.base_interval_seconds = None
                entry.backoff.max_interval_seconds = None
                entry.backoff.empty_streak = 0
                entry.backoff.next_fire_threshold = None

        data['loops'][name] = entry.to_dict()
        if self._label:
            data['instance_label'] = self._label
        self._write(data)
        return entry

    def unregister(self, name: str) -> bool:
        """Remove a loop from the registry. Returns True if removed, False if absent.

        Also clears the pause sidecar so resurrection of the same name starts clean.
        """
        _validate_name(name)
        data = self._read()
        if name not in data['loops']:
            return False
        del data['loops'][name]
        self._write(data)
        set_loop_paused(self.instance_id, name, False)
        return True

    def set_interval(self, name: str, interval: str) -> LoopEntry:
        _validate_name(name)
        data = self._read()
        if name not in data['loops']:
            raise KeyError(f"Loop '{name}' not registered")
        data['loops'][name]['interval'] = interval
        self._write(data)
        return LoopEntry.from_dict(name, data['loops'][name])

    def heartbeat(
        self,
        name: str,
        status: str = 'ok',
        message: str | None = None,
        result: str | None = None,
    ) -> LoopEntry:
        """Record a fire — updates last_run/last_status/last_message and,
        when backoff policy is exponential, the streak + next_fire_threshold.

        `result` is the backoff signal (PROPOSAL_LOOP_BACKOFF.md):
          'found' → new work happened — reset streak, threshold = now + base
          'empty' → fire ran cleanly, nothing to do — advance streak
          'fail'  → errored — reset streak, threshold = now + base
                    (failures are retried at base, not delayed)

        If `result` is None, it's inferred from `status`:
          status='ok'   → result='empty' (conservative)
          status='fail' → result='fail'

        If the loop isn't registered yet, auto-registers as a monitor loop.
        """
        _validate_name(name)
        if status not in VALID_STATUS:
            raise ValueError(f"Invalid status '{status}' — must be one of {VALID_STATUS}")
        if result is not None and result not in VALID_RESULT:
            raise ValueError(f"Invalid result '{result}' — must be one of {VALID_RESULT}")

        if result is None:
            result = 'fail' if status == 'fail' else 'empty'

        data = self._read()
        if name not in data['loops']:
            self.register(name=name, kind='monitor', description='auto-registered via heartbeat')
            data = self._read()

        entry = LoopEntry.from_dict(name, data['loops'][name])
        now = datetime.now(tz=UTC)
        entry.last_run = now.isoformat()
        entry.last_status = status
        entry.last_message = message
        entry.last_result = result

        # Backoff math (no-op when policy=none).
        if entry.backoff.policy == 'exponential' and entry.backoff.base_interval_seconds:
            if result == 'empty':
                entry.backoff.empty_streak += 1
            else:  # 'found' or 'fail' → reset
                entry.backoff.empty_streak = 0
            interval_s = entry.backoff.current_interval_seconds() or entry.backoff.base_interval_seconds
            threshold = now.timestamp() + interval_s
            entry.backoff.next_fire_threshold = (
                datetime.fromtimestamp(threshold, tz=UTC).isoformat()
            )

        data['loops'][name] = entry.to_dict()
        self._write(data)
        return entry

    def should_fire(self, name: str) -> tuple[bool, str]:
        """Check whether the loop body should do work this fire.

        Returns (should_fire, reason). Used by `empirica loop should-fire <NAME>`
        which the loop body calls right after the pause check.

        Reasons:
          'no policy'       → backoff disabled, always fire
          'past threshold'  → wall clock has passed next_fire_threshold
          'no threshold'    → policy enabled but threshold not yet set
          'before threshold' → still in backoff window, skip this fire
          'no loop'         → not registered (treat as fire — caller decides)
        """
        entry = self.get(name)
        if entry is None:
            return True, 'no loop'
        if entry.backoff.policy != 'exponential':
            return True, 'no policy'
        threshold_iso = entry.backoff.next_fire_threshold
        if not threshold_iso:
            return True, 'no threshold'
        try:
            threshold = datetime.fromisoformat(threshold_iso.replace('Z', '+00:00'))
            if threshold.tzinfo is None:
                threshold = threshold.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return True, 'invalid threshold'
        if datetime.now(tz=UTC) >= threshold:
            return True, 'past threshold'
        return False, 'before threshold'

    def poke(self, name: str) -> LoopEntry | None:
        """Manual escape hatch — zero the streak and clear the threshold.

        For when the user knows new work just arrived and doesn't want to
        wait through backoff. Returns the updated entry, or None if the
        loop isn't registered.
        """
        _validate_name(name)
        data = self._read()
        if name not in data['loops']:
            return None
        entry = LoopEntry.from_dict(name, data['loops'][name])
        entry.backoff.empty_streak = 0
        entry.backoff.next_fire_threshold = None
        data['loops'][name] = entry.to_dict()
        self._write(data)
        return entry

    def to_dict(self) -> dict[str, Any]:
        return self._read()
