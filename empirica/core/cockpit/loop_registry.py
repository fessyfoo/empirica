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
    ) -> LoopEntry:
        """Register a loop. Idempotent on (instance_id, name).

        Re-registering an existing loop preserves last_run/last_status/last_message
        and registered_at, but updates kind/cron/interval/description. This lets
        callers safely re-issue `register` at startup without losing heartbeat
        history.
        """
        _validate_name(name)
        _validate_kind(kind)

        data = self._read()
        existing = data['loops'].get(name)

        if existing:
            # Preserve runtime state, update declarative fields.
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
    ) -> LoopEntry:
        """Record a fire — last_run, last_status, last_message.

        If the loop isn't registered yet (e.g. first fire of a re-issued cron
        that hasn't run register yet), this auto-registers it as a 'monitor'
        loop with no schedule. The cron/interval can be filled in by a later
        explicit register call.
        """
        _validate_name(name)
        if status not in VALID_STATUS:
            raise ValueError(f"Invalid status '{status}' — must be one of {VALID_STATUS}")

        data = self._read()
        if name not in data['loops']:
            self.register(name=name, kind='monitor', description='auto-registered via heartbeat')
            data = self._read()

        data['loops'][name]['last_run'] = _now_iso()
        data['loops'][name]['last_status'] = status
        data['loops'][name]['last_message'] = message
        self._write(data)
        return LoopEntry.from_dict(name, data['loops'][name])

    def to_dict(self) -> dict[str, Any]:
        return self._read()
