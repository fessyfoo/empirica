"""Project identity migration — slug→UUID (the 1.12 single-UUID model).

``.empirica/project.yaml`` is the git-intrinsic source of truth for
``project_id`` (a UUID). Legacy projects init'd before the UUID switch carry
``project_id: <slug>`` (e.g. ``empirica``, ``empirica-outreach``). From 1.12 we
kill slug-as-id entirely: a project's identity is a single UUID, and the
practice/``ai_id`` identity stays the project *name*.

This module is the migration engine. It is deliberately self-contained and
dependency-injected (cortex resolution + minting are passed in) so it can be
unit-tested with zero network and wired into ``setup-claude-code --force`` (the
named migration path) and the session-init heal.

Resolution order for a slug/absent yaml id (project.yaml-authoritative):

1. ``project.yaml`` already holds a UUID → it wins, nothing to resolve.
2. ``workspace.db`` ``global_projects.trajectory_path`` → local, fast.
3. Cortex by slug+tenant → the public-release / no-workspace.db case.
4. Mint → never-registered, local-only.
5. Unresolved → a structured, actionable message (no guessing). A remote
   Claude — or the user running ``project-register`` once Cortex is reachable —
   finishes the job.

The re-key is **schema-introspection based**: every table carrying a
``project_id`` column, in every ``.db`` under ``<project>/.empirica/``, gets
``UPDATE … SET project_id = <new> WHERE project_id = <old>``. Complete by
construction — it does not drift as the schema grows new tables. ``workspace.db``
is keyed by ``trajectory_path`` (its ``global_projects.id`` is the UUID), so it
is handled by its own session-init heal, not re-keyed here.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

# Same shape the session-init healer uses (session-init.py:_PROJECT_ID_UUID_RE).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Type alias: (slug, tenant) -> canonical UUID or None.
CortexResolver = Callable[[str | None, str | None], str | None]


def is_uuid(value: object) -> bool:
    """True iff value is a canonical UUID string (the 1.12 project_id shape)."""
    return bool(value) and bool(_UUID_RE.match(str(value)))


def _read_yaml_project_id(project_root: str | Path) -> str | None:
    import yaml

    yaml_path = Path(project_root) / ".empirica" / "project.yaml"
    if not yaml_path.exists():
        return None
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        return cfg.get("project_id") or None
    except Exception:
        return None


def _write_yaml_project_id(project_root: str | Path, new_id: str) -> bool:
    """Atomically rewrite project.yaml's project_id, preserving key order.

    Returns True if a write happened, False if it was already the new value.
    """
    import yaml

    yaml_path = Path(project_root) / ".empirica" / "project.yaml"
    if not yaml_path.exists():
        return False
    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if cfg.get("project_id") == new_id:
        return False
    cfg["project_id"] = new_id
    yaml_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return True


def _lookup_workspace_uuid(
    project_root: str | Path, workspace_db: str | Path | None = None
) -> str | None:
    """Canonical UUID for a project from workspace.db global_projects, by path."""
    ws = (
        Path(workspace_db)
        if workspace_db
        else Path.home() / ".empirica" / "workspace" / "workspace.db"
    )
    if not ws.exists():
        return None
    trajectory = str(Path(project_root) / ".empirica")
    try:
        conn = sqlite3.connect(str(ws))
        try:
            row = conn.execute(
                "SELECT id FROM global_projects WHERE trajectory_path = ?",
                (trajectory,),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:
        return None


def resolve_canonical_uuid(
    project_root: str | Path,
    *,
    workspace_db: str | Path | None = None,
    cortex_resolver: CortexResolver | None = None,
    tenant: str | None = None,
    slug: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a project's canonical UUID. Returns ``(uuid, source)`` or
    ``(None, None)``.

    ``source`` is one of ``yaml`` | ``workspace`` | ``cortex`` — the store that
    supplied the id. project.yaml wins when it already holds a UUID.
    """
    yaml_id = _read_yaml_project_id(project_root)
    if is_uuid(yaml_id):
        return yaml_id, "yaml"

    ws_uuid = _lookup_workspace_uuid(project_root, workspace_db)
    if is_uuid(ws_uuid):
        return ws_uuid, "workspace"

    if cortex_resolver is not None:
        lookup_slug = slug or yaml_id or Path(project_root).name
        try:
            cortex_uuid = cortex_resolver(lookup_slug, tenant)
            if is_uuid(cortex_uuid):
                return cortex_uuid, "cortex"
        except Exception:
            return None, None  # cortex unreachable / errored — unresolved, never guess

    return None, None


def _tables_with_project_id(conn: sqlite3.Connection) -> list[str]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    out = []
    for table in tables:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if "project_id" in cols:
            out.append(table)
    return out


def rekey_project_id_in_db(
    db_path: str | Path, old_id: str, new_id: str
) -> dict[str, int]:
    """Re-key ``project_id`` from ``old_id`` to ``new_id`` across every table in
    one SQLite db that carries a ``project_id`` column.

    Returns ``{table: rows_updated}`` for tables actually touched. A no-op (and
    empty dict) when the db is missing or ``old_id == new_id``.
    """
    result: dict[str, int] = {}
    if old_id == new_id or not Path(db_path).exists():
        return result
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _tables_with_project_id(conn):
            cur = conn.execute(
                f'UPDATE "{table}" SET project_id = ? WHERE project_id = ?',
                (new_id, old_id),
            )
            if cur.rowcount:
                result[table] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return result


def rekey_project_local_dbs(
    project_root: str | Path, old_id: str, new_id: str
) -> dict[str, dict[str, int]]:
    """Re-key every ``.db`` under ``<project_root>/.empirica/`` (sessions +
    artifacts). Returns ``{db_relpath: {table: rows}}`` for dbs touched."""
    empirica_dir = Path(project_root) / ".empirica"
    out: dict[str, dict[str, int]] = {}
    if not empirica_dir.exists():
        return out
    for db_file in sorted(empirica_dir.rglob("*.db")):
        touched = rekey_project_id_in_db(db_file, old_id, new_id)
        if touched:
            out[str(db_file.relative_to(empirica_dir))] = touched
    return out


def migrate_project_to_uuid(
    project_root: str | Path,
    *,
    workspace_db: str | Path | None = None,
    cortex_resolver: CortexResolver | None = None,
    mint: Callable[[], str] | None = None,
    tenant: str | None = None,
) -> dict:
    """Migrate one project's identity to a single canonical UUID (1.12 model).

    Idempotent. Returns a structured result with a ``status``:

    - ``no_project`` — no ``.empirica/project.yaml``.
    - ``already_uuid`` — project_id is already a UUID; nothing to do.
    - ``migrated`` — slug→UUID resolved + re-keyed; carries ``rekeyed`` counts.
    - ``unresolved`` — could not find/mint a UUID; carries an actionable
      ``message`` instead of guessing (a remote Claude / the user finishes it).
    """
    yaml_id = _read_yaml_project_id(project_root)
    if yaml_id is None:
        return {
            "status": "no_project",
            "message": (
                f"No .empirica/project.yaml at {project_root}. "
                "Run 'empirica project-init' to create it."
            ),
        }
    if is_uuid(yaml_id):
        return {"status": "already_uuid", "project_id": yaml_id}

    slug = yaml_id
    new_id, source = resolve_canonical_uuid(
        project_root,
        workspace_db=workspace_db,
        cortex_resolver=cortex_resolver,
        tenant=tenant,
        slug=slug,
    )
    if new_id is None and mint is not None:
        try:
            minted = mint()
            if is_uuid(minted):
                new_id, source = minted, "minted"
        except Exception:
            new_id = None

    if new_id is None:
        return {
            "status": "unresolved",
            "slug": slug,
            "message": (
                f"Could not resolve a canonical UUID for legacy project_id "
                f"'{slug}'. It is not in workspace.db, not registered in Cortex "
                "(or Cortex is unreachable), and minting was unavailable. "
                "Fix: run 'empirica project-register .' once Cortex is reachable "
                "(it adopts or mints the UUID), then re-run the migration."
            ),
        }

    rekeyed = rekey_project_local_dbs(project_root, slug, new_id)
    yaml_updated = _write_yaml_project_id(project_root, new_id)
    return {
        "status": "migrated",
        "slug": slug,
        "project_id": new_id,
        "source": source,
        "yaml_updated": yaml_updated,
        "rekeyed": rekeyed,
    }
