"""
Workspace Search Command — cross-project entity-navigable semantic search.

Searches the workspace_index Qdrant collection, which contains pointers
to artifacts across all projects, filterable by entity references
(contacts, orgs, engagements).

Usage:
    empirica workspace-search --entity contact/david --task "infrastructure concerns"
    empirica workspace-search --entity org/acme --limit 10
    empirica workspace-search --task "pricing strategy" --output json
"""

import json
import sys


def handle_workspace_search_command(args):
    """Handle workspace-search command."""
    try:
        from empirica.core.qdrant.workspace_index import search_workspace_index

        entity_type = None
        entity_id = None
        task = getattr(args, "task", None)

        # Parse --entity TYPE/ID format
        entity_arg = getattr(args, "entity", None)
        if entity_arg:
            if "/" in entity_arg:
                parts = entity_arg.split("/", 1)
                entity_type = parts[0]
                entity_id = parts[1]
            else:
                # Assume it's an entity_id, try to infer type
                entity_id = entity_arg

        if not task and not entity_type:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Provide --task (semantic query) and/or --entity TYPE/ID",
                    }
                )
            )
            sys.exit(1)

        project_id = getattr(args, "project_id", None)
        limit = getattr(args, "limit", 20) or 20
        output_format = getattr(args, "output", "json")

        results = search_workspace_index(
            query_text=task,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            limit=limit,
        )

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "count": len(results),
                        "query": {"task": task, "entity_type": entity_type, "entity_id": entity_id},
                        "results": results,
                    },
                    indent=2,
                )
            )
        else:
            if not results:
                print("No results found.")
                return

            print(f"\n  Workspace Search: {len(results)} results\n")
            for i, r in enumerate(results, 1):
                score = r.get("score", 0)
                atype = r.get("artifact_type", "?")
                text = r.get("text", "")[:120]
                proj = r.get("project_id", "?")[:8]
                entities = []
                for cid in r.get("contact_ids", []):
                    entities.append(f"contact/{cid}")
                for oid in r.get("org_ids", []):
                    entities.append(f"org/{oid}")
                for eid in r.get("engagement_ids", []):
                    entities.append(f"eng/{eid}")
                entity_str = ", ".join(entities) if entities else "no entities"

                print(f"  {i}. [{atype}] (score={score:.3f}, project={proj})")
                print(f"     {text}")
                print(f"     entities: {entity_str}")
                print()

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
