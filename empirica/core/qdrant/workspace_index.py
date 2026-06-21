"""
Workspace Index: cross-project entity-navigable search via Qdrant.

Single global collection containing lightweight pointers to artifacts across
all per-project collections, enriched with flattened entity references
(contact_ids, org_ids, engagement_ids) for filtered search.

Non-engineers navigate by entity (contact, org, engagement) rather than project.
Engineers get cross-repo findings as a bonus.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)

WORKSPACE_INDEX_COLLECTION = "workspace_index"


def _workspace_index_collection() -> str:
    """Global workspace index collection name."""
    return WORKSPACE_INDEX_COLLECTION


def _ensure_collection(client, Distance, VectorParams) -> bool:
    """Lazy-create workspace_index collection if absent."""
    coll = _workspace_index_collection()
    if not client.collection_exists(coll):
        vector_size = _get_vector_size()
        client.create_collection(
            coll,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info(f"Created {coll} collection with vector size {vector_size}")
    return True


def _flatten_entity_refs(entity_refs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Flatten entity_refs list into per-type ID arrays for Qdrant filtering.

    Input:  [{"type": "contact", "id": "david"}, {"type": "org", "id": "acme"}]
    Output: {"contact_ids": ["david"], "org_ids": ["acme"], "engagement_ids": []}
    """
    result = {"contact_ids": [], "org_ids": [], "engagement_ids": []}
    for ref in entity_refs or []:
        etype = ref.get("type", "").lower()
        eid = ref.get("id", "")
        if not eid:
            continue
        if etype in ("contact", "client"):
            result["contact_ids"].append(eid)
        elif etype in ("org", "organization"):
            result["org_ids"].append(eid)
        elif etype == "engagement":
            result["engagement_ids"].append(eid)
    # Deduplicate
    for k in result:
        result[k] = list(set(result[k]))
    return result


def embed_to_workspace_index(
    artifact_id: str,
    artifact_type: str,
    text: str,
    project_id: str,
    entity_refs: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    ai_id: str | None = None,
    timestamp: float | None = None,
    source_collection: str | None = None,
    domain_tags: list[str] | None = None,
) -> bool:
    """Embed an artifact pointer to workspace_index with entity payload.

    Returns True if successful, False if Qdrant unavailable or embedding failed.
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False

        _ensure_collection(client, Distance, VectorParams)

        vector = _get_embedding_safe(text)
        if vector is None:
            return False

        flat = _flatten_entity_refs(entity_refs)

        payload = {
            "source_collection": source_collection or "",
            "project_id": project_id,
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
            "text": text[:300] if text else "",
            "contact_ids": flat["contact_ids"],
            "org_ids": flat["org_ids"],
            "engagement_ids": flat["engagement_ids"],
            "domain_tags": domain_tags or [],
            "timestamp": timestamp or time.time(),
            "session_id": session_id or "",
            "ai_id": ai_id or "",
        }

        point_id = int(hashlib.md5(f"wsidx_{artifact_type}:{artifact_id}".encode()).hexdigest()[:15], 16)

        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=_workspace_index_collection(), points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed to workspace_index: {e}")
        return False


def _format_point(point, score: float) -> dict:
    """Extract payload fields from a Qdrant point into a result dict."""
    p = point.payload or {}
    return {
        "score": score,
        "artifact_id": p.get("artifact_id", ""),
        "artifact_type": p.get("artifact_type", ""),
        "text": p.get("text", ""),
        "project_id": p.get("project_id", ""),
        "contact_ids": p.get("contact_ids", []),
        "org_ids": p.get("org_ids", []),
        "engagement_ids": p.get("engagement_ids", []),
        "domain_tags": p.get("domain_tags", []),
        "session_id": p.get("session_id", ""),
        "timestamp": p.get("timestamp", 0),
    }


def search_workspace_index(
    query_text: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_filter: dict[str, list[str]] | None = None,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search workspace_index with semantic query and/or entity filters.

    Args:
        query_text: Semantic search query (required if no entity filter)
        entity_type: Shorthand filter — "contact", "org", "engagement"
        entity_id: Used with entity_type for simple filtering
        entity_filter: Dict of {"contact_ids": [...], "org_ids": [...], ...}
        project_id: Optional project scope filter
        limit: Maximum results

    Returns:
        List of matching artifact pointers with scores and metadata
    """
    if not _check_qdrant_available():
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []

        coll = _workspace_index_collection()
        if not client.collection_exists(coll):
            return []

        # Build Qdrant filter
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        conditions = []

        # Entity type/id shorthand → filter
        if entity_type and entity_id:
            field_map = {
                "contact": "contact_ids",
                "client": "contact_ids",
                "org": "org_ids",
                "organization": "org_ids",
                "engagement": "engagement_ids",
            }
            field = field_map.get(entity_type.lower())
            if field:
                conditions.append(FieldCondition(key=field, match=MatchAny(any=[entity_id])))

        # Explicit entity_filter dict
        if entity_filter:
            for key in ("contact_ids", "org_ids", "engagement_ids"):
                ids = entity_filter.get(key, [])
                if ids:
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=ids)))

        # Project scope
        if project_id:
            conditions.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))

        query_filter = Filter(must=conditions) if conditions else None

        # Semantic search if query_text provided
        if query_text:
            qvec = _get_embedding_safe(query_text)
            if qvec is None:
                return []

            results = client.query_points(
                collection_name=coll,
                query=qvec,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            return [_format_point(r, r.score) for r in results.points]

        if query_filter:
            # Entity-only query (no semantic component) — scroll with filter
            results = client.scroll(
                collection_name=coll,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            # scroll returns (points, next_offset) tuple
            points = results[0] if isinstance(results, tuple) else results
            return [_format_point(p, 1.0) for p in points]

        logger.warning("workspace_index search requires query_text or entity filter")
        return []

    except Exception as e:
        logger.warning(f"workspace_index search failed: {e}")
        return []


def sync_transaction_to_index(
    project_id: str,
    session_id: str,
    transaction_id: str,
) -> int:
    """Sync entity-linked artifacts from this transaction to workspace_index.

    Reads entity_artifacts table in workspace.db for the given transaction,
    fetches artifact text from sessions.db, and embeds pointers to workspace_index.

    Returns count of points embedded.
    """
    if not _check_qdrant_available():
        return 0

    try:
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository
    except ImportError:
        logger.debug("WorkspaceDBRepository not available")
        return 0

    count = 0
    try:
        with WorkspaceDBRepository.open() as ws_repo:
            links = ws_repo.get_entity_artifacts_by_transaction(transaction_id)

        if not links:
            return 0

        # Group links by artifact to merge entity refs
        artifact_map: dict[str, dict] = {}
        for link in links:
            key = f"{link['artifact_type']}:{link['artifact_id']}"
            if key not in artifact_map:
                artifact_map[key] = {
                    "artifact_type": link["artifact_type"],
                    "artifact_id": link["artifact_id"],
                    "artifact_source": link.get("artifact_source", ""),
                    "entity_refs": [],
                }
            artifact_map[key]["entity_refs"].append(
                {
                    "type": link["entity_type"],
                    "id": link["entity_id"],
                    "relationship": link.get("relationship", "about"),
                }
            )

        # Fetch artifact text from sessions.db
        artifact_texts = _resolve_artifact_texts(
            project_id,
            [(a["artifact_type"], a["artifact_id"]) for a in artifact_map.values()],
        )

        for key, artifact in artifact_map.items():
            text = artifact_texts.get(key, "")
            if not text:
                continue

            source_coll = f"project_{project_id}_memory"
            ok = embed_to_workspace_index(
                artifact_id=artifact["artifact_id"],
                artifact_type=artifact["artifact_type"],
                text=text,
                project_id=project_id,
                entity_refs=artifact["entity_refs"],
                session_id=session_id,
                source_collection=source_coll,
            )
            if ok:
                count += 1

    except Exception as e:
        logger.debug(f"sync_transaction_to_index failed (non-fatal): {e}")

    return count


# Table mapping artifact type → (SQL query, text formatter)
# Formatter takes a row tuple and returns the text string.
_ARTIFACT_QUERIES: dict[str, tuple[str, Any]] = {
    "finding": ("SELECT finding FROM findings WHERE id = ?", lambda r: r[0]),
    "unknown": ("SELECT unknown FROM unknowns WHERE id = ?", lambda r: r[0]),
    "dead_end": ("SELECT approach, why_failed FROM dead_ends WHERE id = ?", lambda r: f"{r[0]} — {r[1]}"),
    "decision": ("SELECT choice, rationale FROM decisions WHERE id = ?", lambda r: f"{r[0]}: {r[1]}"),
    "assumption": ("SELECT assumption FROM assumptions WHERE id = ?", lambda r: r[0]),
    "mistake": ("SELECT mistake, why_wrong FROM mistakes WHERE id = ?", lambda r: f"{r[0]} — {r[1]}"),
    "goal": ("SELECT objective FROM goals WHERE id = ?", lambda r: r[0]),
}


def _resolve_artifact_texts(
    project_id: str,
    artifacts: list[tuple],
) -> dict[str, str]:
    """Resolve artifact text from sessions.db.

    Args:
        project_id: Project UUID
        artifacts: List of (artifact_type, artifact_id) tuples

    Returns:
        Dict keyed by "type:id" → text content
    """
    result = {}
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        for atype, aid in artifacts:
            query_info = _ARTIFACT_QUERIES.get(atype)
            if not query_info:
                continue
            sql, formatter = query_info
            try:
                row = db.conn.execute(sql, (aid,)).fetchone()
                if row:
                    text = formatter(row)
                    if text:
                        result[f"{atype}:{aid}"] = text
            except Exception:
                pass

        db.close()
    except Exception as e:
        logger.debug(f"Artifact text resolution failed: {e}")

    return result
