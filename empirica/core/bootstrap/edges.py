"""Edge walker — depth=1 fold across surfaced artifacts.

For every artifact in the bootstrap output, walk `artifact_edges` once
outward and populate `related_to[]` with neighbors. Single batched query
across all source IDs.

Mirror logic from the daemon's _attach_related_to (api/routes/artifacts.py)
but operates on the bootstrap output structure.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


# Same map as the daemon route — type → (table, id_col)
_TYPE_TABLE_MAP: list[tuple[str, str, str]] = [
    ("finding", "project_findings", "id"),
    ("unknown", "project_unknowns", "id"),
    ("dead_end", "project_dead_ends", "id"),
    ("mistake", "mistakes_made", "id"),
    ("assumption", "assumptions", "id"),
    ("decision", "decisions", "id"),
    ("source", "epistemic_sources", "id"),
    ("goal", "goals", "id"),
]

# Per-type "primary text" column for neighbor summary
_TYPE_TEXT_COL: dict[str, tuple[str, str]] = {
    "finding": ("project_findings", "finding"),
    "unknown": ("project_unknowns", "unknown"),
    "dead_end": ("project_dead_ends", "approach"),
    "mistake": ("mistakes_made", "mistake"),
    "assumption": ("assumptions", "assumption"),
    "decision": ("decisions", "choice"),
    "source": ("epistemic_sources", "title"),
    "goal": ("goals", "objective"),
}


def attach_edges_to_payload(project_path: Path | str, payload: dict) -> None:  # noqa: C901 — multi-step walker, reads linearly
    """In-place fold of depth=1 edges into every artifact across the payload.

    Walks the three-circle wire shape (active_state, persistent_reference,
    topic_relevant_backlog), collects all artifact IDs, runs ONE batched
    query against artifact_edges, batched type-resolution for neighbors,
    then assigns related_to[] to each item.
    """
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return

    # 1. Collect all source IDs from the three circles
    source_ids: list[str] = []
    for circle_key in ("active_state", "persistent_reference", "topic_relevant_backlog"):
        circle = payload.get(circle_key, {})
        for items in circle.values():
            if isinstance(items, list):
                for item in items:
                    if "id" in item:
                        source_ids.append(item["id"])

    if not source_ids:
        return

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        # 2. One batched edge query for all source IDs
        placeholders = ",".join("?" * len(source_ids))
        try:
            cur.execute(
                f"SELECT from_id, to_id, relation FROM artifact_edges "
                f"WHERE from_id IN ({placeholders})",
                source_ids,
            )
            raw_edges = cur.fetchall()
        except sqlite3.OperationalError:
            # artifact_edges table missing (fresh DB pre-migration 041)
            return

        if not raw_edges:
            return

        # 3. Resolve neighbor types + summaries in one pass per artifact table
        neighbor_ids = list({e[1] for e in raw_edges})
        neighbor_meta: dict[str, dict] = {}
        if neighbor_ids:
            ph = ",".join("?" * len(neighbor_ids))
            for atype, table, _id_col in _TYPE_TABLE_MAP:
                text_col = _TYPE_TEXT_COL.get(atype, (table, "id"))[1]
                try:
                    cur.execute(
                        f"SELECT id, {text_col} FROM {table} "
                        f"WHERE id IN ({ph})",
                        neighbor_ids,
                    )
                    for row in cur.fetchall():
                        if row[0] not in neighbor_meta:
                            neighbor_meta[row[0]] = {
                                "type": atype,
                                "summary": (row[1] or "")[:80] if row[1] else "",
                            }
                except sqlite3.OperationalError:
                    continue

        # 4. Group edges by from_id with neighbor metadata
        edges_by_from: dict[str, list[dict]] = {}
        for from_id, to_id, relation in raw_edges:
            meta = neighbor_meta.get(to_id, {"type": "unknown", "summary": ""})
            edges_by_from.setdefault(from_id, []).append({
                "id": to_id,
                "type": meta["type"],
                "relation": relation,
                "summary": meta["summary"],
            })

        # 5. In-place fold into each item across all circles
        for circle_key in ("active_state", "persistent_reference", "topic_relevant_backlog"):
            circle = payload.get(circle_key, {})
            for items in circle.values():
                if isinstance(items, list):
                    for item in items:
                        item_id = item.get("id")
                        if item_id:
                            item["related_to"] = edges_by_from.get(item_id, [])
    finally:
        conn.close()


def collect_all_ids(payload: dict) -> list[str]:
    """Helper for tests: extract all artifact IDs from the payload."""
    ids: list[str] = []
    for circle_key in ("active_state", "persistent_reference", "topic_relevant_backlog"):
        circle = payload.get(circle_key, {})
        for items in circle.values():
            if isinstance(items, list):
                for item in items:
                    if "id" in item:
                        ids.append(item["id"])
    return ids
