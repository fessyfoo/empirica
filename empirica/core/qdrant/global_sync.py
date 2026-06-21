"""
Global learnings: cross-project knowledge aggregation and dead-end detection.
"""

from __future__ import annotations

from empirica.core.qdrant.collections import (
    _eidetic_collection,
    _episodic_collection,
    _global_learnings_collection,
    _memory_collection,
)
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)


def embed_to_global(
    item_id: str,
    text: str,
    item_type: str,
    project_id: str,
    session_id: str | None = None,
    impact: float | None = None,
    resolved_by: str | None = None,
    timestamp: str | None = None,
    tags: list[str] | None = None,
) -> bool:
    """
    Embed a high-impact item to global learnings collection.
    Use for findings with impact > 0.7, resolved unknowns, and significant dead ends.

    Returns True if successful, False if Qdrant not available.
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _global_learnings_collection()

        # Ensure collection exists
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        vector = _get_embedding_safe(text)
        if vector is None:
            return False

        payload = {
            "type": item_type,
            "text": text[:500] if text else None,
            "text_full": text if len(text) <= 500 else None,
            "project_id": project_id,
            "session_id": session_id,
            "impact": impact,
            "resolved_by": resolved_by,
            "timestamp": timestamp,
            "tags": tags or [],
        }

        # Use hash of item_id for numeric Qdrant point ID
        import hashlib

        point_id = int(hashlib.md5(f"global_{item_id}".encode()).hexdigest()[:15], 16)

        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed to global: {e}")
        return False


def search_global(
    query_text: str, item_types: list[str] | None = None, min_impact: float | None = None, limit: int = 10
) -> list[dict]:
    """
    Search global learnings across all projects.

    Args:
        query_text: Semantic search query
        item_types: Filter by type (finding, unknown_resolved, dead_end)
        min_impact: Filter by minimum impact score
        limit: Maximum results

    Returns:
        List of matching items with scores and metadata
    """
    if not _check_qdrant_available():
        return []

    qvec = _get_embedding_safe(query_text)
    if qvec is None:
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []
        coll = _global_learnings_collection()

        if not client.collection_exists(coll):
            return []

        # Build filter if needed
        query_filter = None
        if item_types or min_impact:
            from qdrant_client.models import FieldCondition, Filter, MatchAny, Range

            conditions = []
            if item_types:
                conditions.append(FieldCondition(key="type", match=MatchAny(any=item_types)))
            if min_impact:
                conditions.append(FieldCondition(key="impact", range=Range(gte=min_impact)))
            if conditions:
                query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll, query=qvec, query_filter=query_filter, limit=limit, with_payload=True
        )

        return [
            {
                "score": getattr(r, "score", 0.0) or 0.0,
                "type": (r.payload or {}).get("type"),
                "text": (r.payload or {}).get("text"),
                "project_id": (r.payload or {}).get("project_id"),
                "session_id": (r.payload or {}).get("session_id"),
                "impact": (r.payload or {}).get("impact"),
                "tags": (r.payload or {}).get("tags", []),
            }
            for r in results.points
        ]
    except Exception as e:
        logger.debug(f"search_global failed: {e}")
        return []


def search_cross_project(
    query_text: str,
    exclude_project_id: str | None = None,
    collections_to_search: list[str] | None = None,
    limit: int = 5,
    min_points: int = 1,
) -> list[dict]:
    """
    Search across ALL registered projects' Qdrant collections.

    Unlike search_global() which only queries the global_learnings collection,
    this iterates all project_*_{collection} collections in Qdrant and returns
    merged, ranked results.

    Args:
        query_text: Semantic search query
        exclude_project_id: Skip this project (usually the current one)
        collections_to_search: Which collection types to search.
            Default: ["memory", "eidetic", "episodic"]
        limit: Max results per project per collection type
        min_points: Skip collections with fewer points than this
    Returns:
        List of results sorted by score, tagged with source project_id
    """
    if not _check_qdrant_available():
        return []

    qvec = _get_embedding_safe(query_text)
    if qvec is None:
        return []

    client = _get_qdrant_client()
    if client is None:
        return []

    if collections_to_search is None:
        collections_to_search = ["memory", "eidetic", "episodic"]

    # Discover all project IDs from Qdrant collection names
    all_collections = [c.name for c in client.get_collections().collections]
    project_ids = set()
    for cname in all_collections:
        if cname.startswith("project_") and "_" in cname[8:]:
            pid = cname[8 : cname.rindex("_")]
            project_ids.add(pid)

    if exclude_project_id:
        project_ids.discard(exclude_project_id)

    # Collection name builders
    collection_builders = {
        "memory": _memory_collection,
        "eidetic": _eidetic_collection,
        "episodic": _episodic_collection,
    }

    all_results = []

    for pid in project_ids:
        for coll_type in collections_to_search:
            builder = collection_builders.get(coll_type)
            if not builder:
                continue

            coll_name = builder(pid)
            if coll_name not in all_collections:
                continue

            hits = _query_collection_safe(client, coll_name, qvec, limit, min_points)
            for r in hits:
                result = _build_cross_project_result(r, pid, coll_type)
                all_results.append(result)

    deduped = _deduplicate_results(all_results)
    deduped.sort(key=lambda x: x["score"], reverse=True)
    return deduped[: limit * 3]  # Return more results since they span projects


def _query_collection_safe(client, coll_name, qvec, limit, min_points):
    """Query a Qdrant collection, returning empty list on failure."""
    try:
        info = client.get_collection(coll_name)
        if info.points_count < min_points:
            return []

        hits = client.query_points(
            collection_name=coll_name,
            query=qvec,
            limit=limit,
            with_payload=True,
        )
        return hits.points
    except Exception as e:
        logger.debug(f"cross-project search {coll_name} failed: {e}")
        return []


def _build_cross_project_result(r, pid, coll_type):
    """Build a result dict from a Qdrant point for cross-project search."""
    payload = r.payload or {}
    score = getattr(r, "score", 0.0) or 0.0

    result = {
        "score": score,
        "project_id": pid,
        "collection_type": coll_type,
        "type": payload.get("type", coll_type),
    }

    if coll_type == "memory":
        result["text"] = payload.get("text", "")
        result["impact"] = payload.get("impact")
        result["session_id"] = payload.get("session_id")
    elif coll_type == "eidetic":
        result["content"] = payload.get("content", "")
        result["confidence"] = payload.get("confidence")
        result["domain"] = payload.get("domain")
    elif coll_type == "episodic":
        result["narrative"] = payload.get("narrative", "")
        result["outcome"] = payload.get("outcome")

    return result


def _deduplicate_results(all_results):
    """Deduplicate results by content across collection types."""
    seen_content = {}
    deduped = []
    for r in all_results:
        text = r.get("text") or r.get("content") or r.get("narrative") or ""
        key = " ".join(text.strip().lower().split())
        if key and key in seen_content:
            if r["score"] > seen_content[key]["score"]:
                deduped.remove(seen_content[key])
                seen_content[key] = r
                deduped.append(r)
        else:
            if key:
                seen_content[key] = r
            deduped.append(r)
    return deduped


def sync_high_impact_to_global(project_id: str, min_impact: float = 0.7) -> int:
    """
    Sync high-impact findings and resolved unknowns from a project to global collection.
    Called during project-embed --global or manually.

    Returns number of items synced.
    """
    if not _check_qdrant_available():
        return 0

    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        synced = 0

        # Get high-impact findings
        cursor = db.conn.cursor()
        cursor.execute(
            """
            SELECT id, finding, impact, session_id, created_timestamp
            FROM project_findings
            WHERE project_id = ? AND impact >= ?
        """,
            (project_id, min_impact),
        )

        for row in cursor.fetchall():
            if embed_to_global(
                item_id=row[0],
                text=row[1],
                item_type="finding",
                project_id=project_id,
                session_id=row[3],
                impact=row[2],
                timestamp=str(row[4]),
            ):
                synced += 1

        # Get resolved unknowns (these contain valuable resolution patterns)
        cursor.execute(
            """
            SELECT id, unknown, resolved_by, session_id, resolved_timestamp
            FROM project_unknowns
            WHERE project_id = ? AND is_resolved = 1 AND resolved_by IS NOT NULL
        """,
            (project_id,),
        )

        for row in cursor.fetchall():
            resolution_text = f"Unknown: {row[1]}\nResolved by: {row[2]}"
            if embed_to_global(
                item_id=row[0],
                text=resolution_text,
                item_type="unknown_resolved",
                project_id=project_id,
                session_id=row[3],
                resolved_by=row[2],
                timestamp=str(row[4]) if row[4] else None,
            ):
                synced += 1

        # Get dead ends (anti-patterns to avoid)
        cursor.execute(
            """
            SELECT id, approach, why_failed, session_id, created_timestamp
            FROM project_dead_ends
            WHERE project_id = ?
        """,
            (project_id,),
        )

        for row in cursor.fetchall():
            deadend_text = f"Approach: {row[1]}\nWhy failed: {row[2]}"
            if embed_to_global(
                item_id=row[0],
                text=deadend_text,
                item_type="dead_end",
                project_id=project_id,
                session_id=row[3],
                timestamp=str(row[4]),
            ):
                synced += 1

        db.close()
        return synced
    except Exception as e:
        logger.warning(f"Failed to sync to global: {e}")
        return 0


# ============================================================================
# DEAD END SPECIFIC - Branch divergence and anti-pattern detection
# ============================================================================


def embed_dead_end_with_branch_context(
    project_id: str,
    dead_end_id: str,
    approach: str,
    why_failed: str,
    session_id: str | None = None,
    branch_id: str | None = None,
    winning_branch_id: str | None = None,
    score_diff: float | None = None,
    preflight_vectors: dict | None = None,
    postflight_vectors: dict | None = None,
    timestamp: str | None = None,
) -> bool:
    """
    Embed a dead end with full branch context for similarity search.
    Use when a branch loses epistemic merge - captures divergence pattern.

    Args:
        project_id: Project ID
        dead_end_id: Unique ID for this dead end
        approach: Description of the approach that failed
        why_failed: Reason for failure
        session_id: Session ID
        branch_id: ID of the losing branch
        winning_branch_id: ID of the winning branch
        score_diff: Epistemic score difference
        preflight_vectors: Initial epistemic vectors
        postflight_vectors: Final epistemic vectors
        timestamp: When this dead end was recorded

    Returns:
        True if successful, False if Qdrant not available
    """
    if not _check_qdrant_available():
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False
        coll = _memory_collection(project_id)

        # Ensure collection exists
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        # Rich text for embedding - captures what was tried and why it failed
        text = f"Dead end approach: {approach}\nWhy failed: {why_failed}"

        vector = _get_embedding_safe(text)
        if vector is None:
            return False

        # Rich payload for filtering and analysis
        payload = {
            "type": "dead_end",
            "text": text[:500],
            "approach": approach,
            "why_failed": why_failed,
            "session_id": session_id,
            "branch_id": branch_id,
            "winning_branch_id": winning_branch_id,
            "score_diff": score_diff,
            "preflight_vectors": preflight_vectors,
            "postflight_vectors": postflight_vectors,
            "timestamp": timestamp,
            "is_branch_deadend": branch_id is not None,
        }

        # Use hash of dead_end_id for numeric Qdrant point ID
        import hashlib

        point_id = int(hashlib.md5(dead_end_id.encode()).hexdigest()[:15], 16)

        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed dead end with branch context: {e}")
        return False


def search_similar_dead_ends(
    project_id: str, query_approach: str, include_branch_deadends: bool = True, limit: int = 5
) -> list[dict]:
    """
    Search for similar past dead ends before starting a new approach.
    Use this in NOETIC phase to avoid repeating known failures.

    Args:
        project_id: Project ID
        query_approach: Description of the approach you're considering
        include_branch_deadends: Include dead ends from branch divergence
        limit: Maximum results

    Returns:
        List of similar dead ends with scores and context
    """
    if not _check_qdrant_available():
        return []

    qvec = _get_embedding_safe(f"Dead end approach: {query_approach}")
    if qvec is None:
        return []

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = _get_qdrant_client()
        if client is None:
            return []
        coll = _memory_collection(project_id)

        if not client.collection_exists(coll):
            return []

        # Filter for dead_end type only
        conditions = [FieldCondition(key="type", match=MatchValue(value="dead_end"))]

        # Optionally filter out branch dead ends
        if not include_branch_deadends:
            conditions.append(FieldCondition(key="is_branch_deadend", match=MatchValue(value=False)))

        query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=coll, query=qvec, query_filter=query_filter, limit=limit, with_payload=True
        )

        return [
            {
                "score": getattr(r, "score", 0.0) or 0.0,
                "approach": (r.payload or {}).get("approach"),
                "why_failed": (r.payload or {}).get("why_failed"),
                "session_id": (r.payload or {}).get("session_id"),
                "branch_id": (r.payload or {}).get("branch_id"),
                "score_diff": (r.payload or {}).get("score_diff"),
                "is_branch_deadend": (r.payload or {}).get("is_branch_deadend", False),
            }
            for r in results.points
        ]
    except Exception as e:
        logger.debug(f"search_similar_dead_ends failed: {e}")
        return []


def search_global_dead_ends(query_approach: str, limit: int = 5) -> list[dict]:
    """
    Search for similar dead ends across ALL projects (global learnings).
    Use to avoid repeating mistakes made in other projects.

    Args:
        query_approach: Description of the approach you're considering
        limit: Maximum results

    Returns:
        List of similar dead ends from any project
    """
    if not _check_qdrant_available():
        return []

    return search_global(query_text=f"Dead end approach: {query_approach}", item_types=["dead_end"], limit=limit)
