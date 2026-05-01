"""Snapshot — canonical scanner output dataclass + the ``collect_snapshot`` orchestrator.

A snapshot is the deterministic Phase-1 emission: a JSON-serializable
dataclass containing each collector's filtered output plus a small header.
No interpretation, no judgement, no classification. Phase 2 will read
``Snapshot.to_dict()`` and add the agent-judgment layer.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .env_names import collect_env_var_names
from .manifests import collect_manifests
from .network import collect_network
from .processes import collect_processes
from .read_surface import DEFAULT_READ_SURFACE, ReadSurface, load_read_surface
from .scheduled import collect_scheduled


@dataclass
class Snapshot:
    """Single ``empirica scan`` snapshot — the deterministic ground truth."""

    scan_id: str
    started_at: float            # epoch seconds
    finished_at: float | None
    host: str
    platform: str
    scanner_pid: int
    snapshot: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=str)

    # ── Convenience accessors ────────────────────────────────────────────
    @property
    def processes(self) -> list[dict[str, Any]]:
        return self.snapshot.get('processes', [])

    @property
    def listening_ports(self) -> list[int]:
        net = self.snapshot.get('network') or {}
        return list(net.get('listening_ports', []))

    @property
    def env_var_names(self) -> list[str]:
        env = self.snapshot.get('process_env') or {}
        return list(env.get('var_names_only', []))


def _safe_collect(snap: Snapshot, name: str, fn,
                  default_payload, default_coverage):
    """Run ``fn`` and return ``(payload, coverage)``.

    On failure, captures the error in ``snap.errors`` and returns the
    supplied defaults so the snapshot can still serialize.
    """
    try:
        result = fn()
    except Exception as exc:
        snap.errors.append(f"{name}: {type(exc).__name__}: {exc}")
        return default_payload, {**default_coverage, 'collector_error': str(exc)}

    if isinstance(result, tuple) and len(result) == 2:
        return result
    # Defensive — old shape (just payload). Should not happen post-refactor.
    return result, default_coverage


def _compute_globs_coverage(surface: ReadSurface,
                            project_root: Path | None = None
                            ) -> dict[str, Any]:
    """Count files matching each ``relevant_globs_for_coverage`` entry.

    Phase 1 only counts file existence — the deterministic 'how big is the
    relevant space' signal. Phase 2 will compare this against what the AI
    judgment layer actually inspected.
    """
    if not surface.relevant_globs_for_coverage:
        return {}

    root = (project_root or Path.cwd()).resolve()
    out: dict[str, Any] = {}
    for category, globs in surface.relevant_globs_for_coverage.items():
        if not isinstance(globs, list):
            continue
        match_count = 0
        per_glob: dict[str, int] = {}
        for pattern in globs:
            if not isinstance(pattern, str):
                continue
            try:
                hits = list(root.glob(pattern))
            except (OSError, ValueError):
                hits = []
            per_glob[pattern] = len(hits)
            match_count += len(hits)
        out[category] = {
            'patterns': per_glob,
            'total_matches': match_count,
        }
    return out


def collect_snapshot(read_surface: ReadSurface | None = None,
                     project_yaml: str | None = None,
                     project_root: Path | None = None) -> Snapshot:
    """Run every collector and assemble a :class:`Snapshot`.

    ``read_surface`` overrides automatic resolution; pass ``None`` to read
    from ``project.yaml`` (default). ``project_root`` controls the base
    directory for ``relevant_globs_for_coverage``; defaults to ``cwd``.
    """
    surface = read_surface or load_read_surface(project_yaml)

    snap = Snapshot(
        scan_id=str(uuid.uuid4()),
        started_at=time.time(),
        finished_at=None,
        host=socket.gethostname(),
        platform=f"{platform.system()} {platform.release()}",
        scanner_pid=os.getpid(),
    )

    proc_rows, proc_cov = _safe_collect(
        snap, 'processes', lambda: collect_processes(surface),
        default_payload=[], default_coverage={'attempted': 0, 'succeeded': 0},
    )
    net_payload, net_cov = _safe_collect(
        snap, 'network', lambda: collect_network(surface),
        default_payload={'connections': [], 'listening_ports': []},
        default_coverage={'attempted': 0, 'succeeded': 0},
    )
    sched_payload, sched_cov = _safe_collect(
        snap, 'scheduled', lambda: collect_scheduled(surface),
        default_payload={}, default_coverage={'sources_checked': 0},
    )
    env_payload, env_cov = _safe_collect(
        snap, 'process_env', lambda: collect_env_var_names(surface),
        default_payload={'var_names_only': []},
        default_coverage={'total_env_vars': 0, 'interesting_matches': 0},
    )
    fs_payload, fs_cov = _safe_collect(
        snap, 'filesystem', lambda: collect_manifests(surface),
        default_payload={}, default_coverage={},
    )

    snap.snapshot['processes'] = proc_rows
    snap.snapshot['network'] = net_payload
    snap.snapshot['scheduled'] = sched_payload
    snap.snapshot['process_env'] = env_payload
    snap.snapshot['filesystem'] = fs_payload

    snap.snapshot['read_surface_summary'] = {
        'process_fields': sorted(surface.process),
        'network_fields': sorted(surface.network),
        'filesystem_fields': sorted(surface.filesystem),
        'process_env_fields': sorted(surface.process_env),
        'scheduled_fields': sorted(surface.scheduled),
        'mcp_fields': sorted(surface.mcp),
    }

    snap.snapshot['coverage'] = {
        'processes': proc_cov,
        'network': net_cov,
        'scheduled': sched_cov,
        'process_env': env_cov,
        'filesystem': fs_cov,
        'relevant_globs': _compute_globs_coverage(surface, project_root),
    }

    snap.finished_at = time.time()
    return snap


__all__ = [
    'DEFAULT_READ_SURFACE',
    'Snapshot',
    'collect_snapshot',
]
