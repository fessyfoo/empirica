"""
Git Abstraction Commands — non-coder-friendly git operations.

`empirica save` — git add + commit with auto-generated message from transaction state
`empirica history` — git log + notes parsed into human-readable epistemic timeline

These commands make git invisible to non-engineers while preserving
version control, sync, and epistemic state persistence.
"""

import json
import logging
import subprocess
import time

logger = logging.getLogger(__name__)


def handle_save_command(args):
    """Handle save command — git add + commit with auto-generated message."""
    try:
        from empirica.config.path_resolver import get_git_root
        from empirica.utils.session_resolver import InstanceResolver as R

        git_root = get_git_root()
        if not git_root:
            print(json.dumps({"ok": False, "error": "Not in a git repository"}))
            return

        # Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=str(git_root),
        )
        if not status.stdout.strip():
            print(json.dumps({"ok": True, "action": "nothing_to_save", "message": "No changes to save"}))
            return

        # Stage tracked files + .empirica/
        subprocess.run(
            ["git", "add", "-u"],
            capture_output=True,
            cwd=str(git_root),
        )
        empirica_dir = git_root / ".empirica"
        if empirica_dir.exists():
            subprocess.run(
                ["git", "add", ".empirica/"],
                capture_output=True,
                cwd=str(git_root),
            )

        # Generate commit message
        message = getattr(args, "message", None)
        if not message:
            tx = R.transaction_read()
            if tx:
                tx_id = tx.get("transaction_id", "")[:8]
                eng = tx.get("active_engagement", "")
                if eng:
                    message = f"empirica: save (transaction {tx_id}, engagement {eng})"
                else:
                    message = f"empirica: save (transaction {tx_id})"
            else:
                message = f"empirica: save ({time.strftime('%Y-%m-%d %H:%M')})"

        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=str(git_root),
        )

        if result.returncode == 0:
            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(git_root),
            )
            commit_hash = hash_result.stdout.strip()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "commit": commit_hash,
                        "message": message,
                    }
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": result.stderr.strip() or "Commit failed",
                    }
                )
            )

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


def handle_history_command(args):
    """Handle history command — epistemic timeline from git log + notes."""
    try:
        from empirica.config.path_resolver import get_git_root

        git_root = get_git_root()
        if not git_root:
            print(json.dumps({"ok": False, "error": "Not in a git repository"}))
            return

        limit = getattr(args, "limit", 20) or 20
        output_format = getattr(args, "output", "human")
        entity_filter = getattr(args, "entity", None)

        # Parse entity filter
        filter_entity_type = None
        filter_entity_id = None
        if entity_filter and "/" in entity_filter:
            parts = entity_filter.split("/", 1)
            filter_entity_type = parts[0]
            filter_entity_id = parts[1]

        # Get git log
        log_result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={limit * 2}",  # Fetch extra for filtering
                "--format=%H|%h|%ai|%s",
            ],
            capture_output=True,
            text=True,
            cwd=str(git_root),
        )

        if log_result.returncode != 0:
            print(json.dumps({"ok": False, "error": "git log failed"}))
            return

        entries = []
        for line in log_result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue

            full_hash, short_hash, date, subject = parts

            # Try to read epistemic notes
            notes = _read_epistemic_notes(full_hash, str(git_root))

            # Entity filtering
            if filter_entity_type and filter_entity_id:
                entity_context = notes.get("entity_context", [])
                match = any(
                    e.get("entity_type") == filter_entity_type and e.get("entity_id") == filter_entity_id
                    for e in entity_context
                )
                if not match:
                    continue

            entry = {
                "commit": short_hash,
                "date": date,
                "subject": subject,
                "phase": notes.get("phase", ""),
                "entity_context": notes.get("entity_context", []),
                "vectors": notes.get("vectors", {}),
            }
            entries.append(entry)

            if len(entries) >= limit:
                break

        if output_format == "json":
            print(json.dumps({"ok": True, "count": len(entries), "entries": entries}, indent=2))
        else:
            if not entries:
                print("No history entries found.")
                return

            print(f"\n  Epistemic Timeline ({len(entries)} entries)\n")
            for e in entries:
                phase = f" [{e['phase']}]" if e["phase"] else ""
                entities = []
                for ec in e.get("entity_context", []):
                    entities.append(f"{ec.get('entity_type', '?')}/{ec.get('entity_id', '?')}")
                entity_str = f" ({', '.join(entities)})" if entities else ""
                know = e.get("vectors", {}).get("know", "")
                know_str = f" know={know}" if know else ""

                print(f"  {e['date'][:16]}  {e['commit']}  {e['subject']}{phase}{know_str}{entity_str}")

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


def _read_epistemic_notes(commit_hash: str, cwd: str) -> dict:
    """Read epistemic state from git notes for a commit."""
    # Try multiple note refs
    for ref in ["breadcrumbs", "empirica-precompact"]:
        try:
            result = subprocess.run(
                ["git", "notes", f"--ref={ref}", "show", commit_hash],
                capture_output=True,
                text=True,
                cwd=cwd,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, Exception):
            continue
    return {}
