"""
Mistake Commands - Log and query mistakes for learning from failures
"""

import json
import logging

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _store_mistake_to_git(
    mistake_id, project_id, session_id, ai_id, mistake, why_wrong, prevention, cost_estimate, root_cause_vector, goal_id
):
    """Store mistake in git notes for sync. Returns True if stored."""
    try:
        from empirica.core.canonical.empirica_git.mistake_store import GitMistakeStore

        git_store = GitMistakeStore()
        stored = git_store.store_mistake(
            mistake_id=mistake_id,
            project_id=project_id,
            session_id=session_id,
            ai_id=ai_id,
            mistake=mistake,
            why_wrong=why_wrong,
            prevention=prevention,
            cost_estimate=cost_estimate,
            root_cause_vector=root_cause_vector,
            goal_id=goal_id,
        )
        if stored:
            logger.info(f"✓ Mistake {mistake_id[:8]} stored in git notes")
        return stored
    except Exception as git_err:
        logger.warning(f"Git notes storage failed: {git_err}")
        return False


def _embed_mistake_to_qdrant(project_id, mistake_id, mistake, prevention, session_id, goal_id):
    """Auto-embed mistake to Qdrant for semantic search. Returns True if embedded."""
    if not (project_id and mistake_id):
        return False
    try:
        from datetime import datetime

        from empirica.core.qdrant.vector_store import embed_single_memory_item

        text = f"MISTAKE: {mistake} Prevention: {prevention or 'none specified'}"
        return embed_single_memory_item(
            project_id=project_id,
            item_id=mistake_id,
            text=text,
            item_type="mistake",
            session_id=session_id,
            goal_id=goal_id,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as embed_err:
        logger.warning(f"Auto-embed failed: {embed_err}")
        return False


def _resolve_mistake_context(db, session_id, project_id):
    """Resolve project_id, transaction_id, and ai_id from DB.

    Returns (project_id, transaction_id, ai_id).
    """
    if not project_id and session_id:
        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row["project_id"]:
            project_id = row["project_id"]

    transaction_id = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        transaction_id = R.transaction_id()
    except Exception:
        pass

    ai_id = "claude-code"
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row["ai_id"]:
            ai_id = row["ai_id"]
    except Exception:
        pass

    return project_id, transaction_id, ai_id


def handle_mistake_log_command(args):
    """Handle mistake-log command"""
    try:
        from empirica.data.session_database import SessionDatabase

        project_id = getattr(args, "project_id", None)
        session_id = getattr(args, "session_id", None)
        mistake = args.mistake
        why_wrong = args.why_wrong
        cost_estimate = getattr(args, "cost_estimate", None)
        root_cause_vector = getattr(args, "root_cause_vector", None)
        prevention = getattr(args, "prevention", None)
        goal_id = getattr(args, "goal_id", None)
        output_format = getattr(args, "output", "json")
        entity_type = getattr(args, "entity_type", None)
        entity_id = getattr(args, "entity_id", None)
        via = getattr(args, "via", None)

        if not session_id:
            from empirica.utils.session_resolver import InstanceResolver as R

            session_id = R.session_id()

        if not session_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No active transaction and --session-id not provided",
                        "hint": "Either run PREFLIGHT first, or provide --session-id explicitly",
                    }
                )
            )
            return

        db = SessionDatabase()
        project_id, transaction_id, ai_id = _resolve_mistake_context(db, session_id, project_id)

        mistake_id = db.log_mistake(
            session_id=session_id,
            mistake=mistake,
            why_wrong=why_wrong,
            cost_estimate=cost_estimate,
            root_cause_vector=root_cause_vector,
            prevention=prevention,
            goal_id=goal_id,
            project_id=project_id,
            transaction_id=transaction_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )

        if entity_type and entity_type != "project" and entity_id:
            try:
                from .artifact_log_commands import _create_entity_artifact_link

                _create_entity_artifact_link(
                    artifact_type="mistake",
                    artifact_id=mistake_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    discovered_via=via,
                    transaction_id=transaction_id,
                )
            except Exception as link_err:
                logger.debug(f"Entity artifact link failed (non-fatal): {link_err}")

        db.close()

        git_stored = _store_mistake_to_git(
            mistake_id,
            project_id,
            session_id,
            ai_id,
            mistake,
            why_wrong,
            prevention,
            cost_estimate,
            root_cause_vector,
            goal_id,
        )

        embedded = _embed_mistake_to_qdrant(project_id, mistake_id, mistake, prevention, session_id, goal_id)

        result = {
            "ok": True,
            "mistake_id": mistake_id,
            "session_id": session_id,
            "project_id": project_id,
            "git_stored": git_stored,
            "embedded": embedded,
            "message": "Mistake logged to project scope",
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Mistake logged successfully")
            print(f"   Mistake ID: {mistake_id[:8]}...")
            print(f"   Session: {session_id[:8]}...")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print("   📝 Stored in git notes for sync")
            if embedded:
                print("   🔍 Auto-embedded for semantic search")
            if root_cause_vector:
                print(f"   Root cause: {root_cause_vector} vector")
            if cost_estimate:
                print(f"   Cost: {cost_estimate}")

        return None

    except Exception as e:
        handle_cli_error(e, "Mistake log", getattr(args, "verbose", False))
        return None


def handle_mistake_query_command(args):
    """Handle mistake-query command"""
    try:
        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        session_id = getattr(args, "session_id", None)
        goal_id = getattr(args, "goal_id", None)
        limit = getattr(args, "limit", 10)

        # Query mistakes
        db = SessionDatabase()
        mistakes = db.get_mistakes(session_id=session_id, goal_id=goal_id, limit=limit)
        db.close()

        # Format output
        if hasattr(args, "output") and args.output == "json":
            result = {
                "ok": True,
                "mistakes_count": len(mistakes),
                "mistakes": [
                    {
                        "mistake_id": m["id"],
                        "session_id": m["session_id"],
                        "goal_id": m["goal_id"],
                        "mistake": m["mistake"],
                        "why_wrong": m["why_wrong"],
                        "cost_estimate": m["cost_estimate"],
                        "root_cause_vector": m["root_cause_vector"],
                        "prevention": m["prevention"],
                        "timestamp": m["created_timestamp"],
                    }
                    for m in mistakes
                ],
            }
            print(json.dumps(result, indent=2))
        else:
            print(f"📋 Found {len(mistakes)} mistake(s):")
            for i, m in enumerate(mistakes, 1):
                print(f"\n{i}. {m['mistake'][:60]}...")
                print(f"   Why wrong: {m['why_wrong'][:60]}...")
                if m["cost_estimate"]:
                    print(f"   Cost: {m['cost_estimate']}")
                if m["root_cause_vector"]:
                    print(f"   Root cause: {m['root_cause_vector']}")
                if m["prevention"]:
                    print(f"   Prevention: {m['prevention'][:60]}...")
                print(f"   Session: {m['session_id'][:8]}...")

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Mistake query", getattr(args, "verbose", False))
        return None
