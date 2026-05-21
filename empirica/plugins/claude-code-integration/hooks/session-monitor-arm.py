#!/usr/bin/env python3
"""SessionStart hook: arm a Monitor to bridge systemd loop fires into this session.

The Phase 1b wake-from-idle bridge for canonical loops scheduled by
systemd-user timers (goal f718156c). The systemd service ExecStart
(`empirica loop tick`) appends one JSON line per fire to
`~/.empirica/loop_fires.log`. This hook tells the running Claude to
arm a persistent Monitor that tails the log and reacts to each event.

Output: hookSpecificOutput.additionalContext (string) — markdown
instructions telling Claude exactly which Monitor to arm and how to
react to fire events. Empty output when no enabled loops exist (or
systemd isn't available, or the instance can't be resolved).

Non-blocking — any failure path emits empty output so a missing
systemd doesn't break SessionStart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Plugin script — empirica package on sys.path via plugin bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    from empirica.core.cockpit.canonical_loops import canonical_loop_by_name
    from empirica.core.loop_scheduler import list_active_loops_for_instance
    from empirica.core.loop_scheduler.persistent_listener import is_listener_running
    from empirica.utils.session_resolver import InstanceResolver
except Exception:
    # Empirica package broken / not installed → emit empty output, exit clean.
    print(json.dumps({}))
    sys.exit(0)


def _build_reaction_table(loop_names: list[str]) -> str:
    """Markdown table mapping each active loop → its body skill."""
    rows = []
    for name in loop_names:
        entry = canonical_loop_by_name(name) or {}
        body_skill = entry.get("body_skill") or name
        rows.append(f"| `{name}` | `/{body_skill}` |")
    if not rows:
        return ""
    return (
        "| Loop name | Body skill to invoke on fire |\n"
        "|---|---|\n"
        + "\n".join(rows)
    )


def _query_listener_on(instance_id: str) -> dict | None:
    """Delegate to `empirica listener on --output json` for the canonical
    arming payload. Returns the parsed JSON or None on any failure.

    Phase 2 of prop_oxrhoehv4 — single source of truth: the CLI handler
    owns the persistent-service short-circuit detection + Monitor command
    rendering. This hook just renders the JSON response as markdown.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [
                "empirica", "listener", "on",
                "--ai-id", instance_id,
                "--instance", instance_id,
                "--output", "json",
            ],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _build_monitor_block_from_cli(payload: dict | None, instance_id: str) -> str:
    """Render the Monitor-arming markdown block from `empirica listener on`'s
    JSON response. Falls back to the canonical default command when the CLI
    is unavailable.
    """
    # Persistent OS service already subscribed → no in-session Monitor needed
    if payload and payload.get("status") == "persistent_service_active":
        return (
            f"**A persistent listener service is already running for this ai_id**\n"
            f"({instance_id}), so you don't need to arm an in-session Monitor —\n"
            f"wake events arrive via the system service (systemd-user / launchd).\n"
            f"Verify status anytime: `empirica loop listen-status --ai-id {instance_id}`."
        )
    # Use the CLI-provided next_step.args when available
    ns = (payload or {}).get("next_step") or {}
    monitor_args = ns.get("args") if isinstance(ns, dict) else None
    if monitor_args:
        description = monitor_args.get("description", f"Cortex orchestration push listener for {instance_id}")
        command = monitor_args.get("command", f"empirica loop listen --instance {instance_id}")
        persistent = monitor_args.get("persistent", True)
    else:
        # Fallback when CLI is unavailable — preserve pre-Phase-2 behavior
        description = f"Cortex orchestration push listener for {instance_id}"
        command = f"empirica loop listen --instance {instance_id}"
        persistent = True
    after_arm = ns.get("after_arm") if isinstance(ns, dict) else None
    after_arm_hint = (
        f"\n\nAfter arming, run `{after_arm}` (replace `<monitor_task_id>` "
        f"with the actual id returned by Monitor) so `empirica listener off` "
        f"knows what to TaskStop later."
        if after_arm else ""
    )
    return (
        f"**Arm this Monitor at session start:**\n\n"
        f"```python\n"
        f"Monitor(\n"
        f'    description="{description}",\n'
        f'    command="{command}",\n'
        f"    persistent={persistent},\n"
        f")\n"
        f"```\n\n"
        f"The listener does the right thing on every failure mode: catches up via\n"
        f"content_poll on initial start, on each push arrival, AND on disconnect\n"
        f"+ reconnect. No periodic timer needed — push-primary, poll-on-reconnect-only\n"
        f'("epistemic email for the AI age" — David, 2026-05-15).{after_arm_hint}'
    )


def _build_additional_context(
    instance_id: str, loop_names: list[str], listener_running: bool = False,
) -> str:
    # T8 (goal f718156c): switched from `tail -F loop_fires.log` to
    # `empirica loop listen` — the listener is the push-primary wake
    # mechanism. It holds an ntfy stream to cortex, emits one stdout
    # line per ECO-decided proposal event.
    #
    # Phase 2 of prop_oxrhoehv4 (2026-05-21): delegate to
    # `empirica listener on --output json` for the canonical short-circuit
    # detection + Monitor command rendering. The CLI is the single source
    # of truth; this hook just renders the JSON as markdown. `listener_running`
    # arg kept for backwards-compat with callers; CLI delegation supersedes.
    payload = _query_listener_on(instance_id)
    if payload is None and listener_running:
        # CLI unavailable but caller already detected persistent service —
        # synthesize a persistent_service_active payload for the renderer.
        payload = {"status": "persistent_service_active"}
    monitor_block = _build_monitor_block_from_cli(payload, instance_id)
    return f"""\
## 📬 Empirica orchestration listener — arm at session start

This instance (`{instance_id}`) has canonical loops registered. The
push-primary wake mechanism is `empirica loop listen` — it holds an
authenticated ntfy stream to Cortex and emits one stdout line per
ECO-decided proposal event (real wake) or AI-to-AI completion ack.

### REQUIRED: load mesh skills before your first transaction

Because this session will receive AI-mesh wake events, BOTH messaging
skills MUST be loaded before your first PREFLIGHT:

- `/cortex-mailbox-poll` — receive side: per-direction × per-status
  reaction protocol for incoming `proposal_event` notifications
- `/cortex-mailbox-send` — send side: how to emit proposals, the
  collab vs ECO-gated flavor split, target ai_id verification, AND
  the completion-ack handshake (without which the source AI never
  knows you delivered)

Loading them only when an event arrives is too late — the send-side
handshake guidance is needed BEFORE you act, not after. Load both now.

### Active loops + their body skills

{_build_reaction_table(loop_names)}

{monitor_block}

**Reaction protocol** — when an event arrives (one JSON line in the chat):

1. Read `direction` field:
   - `inbox` → ECO-decided proposal targeting you (act per status)
   - `outbox` → ack/refinement on a proposal you emitted (informational)
2. **If mid-transaction:** log a goal `"Process <direction>/<status>: <proposal_id>"`
   and pick up at next natural break (EWM pattern).
3. **If idle:** follow the per-direction × per-status reaction protocol
   in the already-loaded `/cortex-mailbox-poll` skill (act on accepted,
   refine on changed, acknowledge on completed). When completing work a
   peer asked of you, ack via the `/cortex-mailbox-send` skill's
   completion-handshake guidance — without it, the source AI never sees
   the work landed.

**ECO-gated autonomy property:** every event you act on traces back to an
ECO actor decision (`eco_actor` field). The listener filters `eco_review`
proposals server-side — they never cross the wake boundary. Even if the
ntfy stream were compromised, your status re-verification against Cortex
by `proposal_id` is the auth boundary.

If you do not arm this Monitor, events will accumulate at Cortex but no
work will trigger in this session. Arming is idempotent (Monitor with
identical command is a no-op the second time).
"""


def main() -> int:
    # Use ai_id (project basename) for timer-name lookup, not the ephemeral
    # tmux pane id. Timers are named `empirica-loop-<ai_id>-<loop>.timer`
    # so they survive tmux restarts; the hook must query with the same
    # stable id the installer used. Falls back to instance_id only for
    # legacy/unconfigured environments. (David, 2026-05-16)
    try:
        instance_id = InstanceResolver.ai_id() or InstanceResolver.instance_id()
    except Exception:
        instance_id = None
    if not instance_id:
        print(json.dumps({}))
        return 0

    try:
        loops = list_active_loops_for_instance(instance_id)
    except Exception:
        loops = []

    if not loops:
        print(json.dumps({}))
        return 0

    # If the persistent listener service (prop_flrtxxn32japbazq) is already
    # running for this ai_id, an in-session Monitor would duplicate the
    # listener — emit a thinner block (skill-load + reaction protocol only)
    # without the Monitor arming instructions.
    listener_running = False
    try:
        listener_running = is_listener_running(instance_id)
    except Exception:
        listener_running = False

    additional = _build_additional_context(instance_id, loops, listener_running)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional,
        },
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
