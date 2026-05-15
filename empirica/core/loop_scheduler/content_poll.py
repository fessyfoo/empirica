"""Content-aware loop tick body for cortex-mailbox-poll.

Replaces the heartbeat model ("fire every 30s, AI decides whether to act")
with a content-aware model ("poll Cortex, emit only when ECO-decided content
arrived or changed status").

## ECO-gated autonomy property

This module is the security/architecture pivot for canonical loops:

  - The systemd timer fires this poll every N seconds. The timer is
    mechanism — easy to hijack at the OS layer.
  - The poll fetches `/v1/orchestration/inbox?status=accepted,changed,declined`
    — only proposals ECO (David, via phone/extension) has decided on.
  - Diffs against last-seen state. Emits one line per new-or-changed proposal.
  - The AI's wake signal carries a `proposal_id` — the action authorization
    traces back to an ECO decision. Hijacking the timer can't widen the
    AI's authority because any forged event points at a proposal_id whose
    status the AI re-verifies with Cortex (which only returns ECO-decided
    state).

If the timer is silent (no new content), the AI stays idle — zero token
cost. If a malicious actor injects events into the fires log, the AI's
verification step against Cortex catches them.

## State file

`~/.empirica/loop_state/<inst>_<loop>.json`:

    {
      "last_poll_ts": "2026-05-15T20:00:00+00:00",
      "proposals": {
        "prop_xyz": {"status": "accepted", "seen_at": "2026-05-15T19:51:53Z"},
        "prop_abc": {"status": "changed",  "seen_at": "2026-05-15T19:30:00Z"}
      }
    }

## Bootstrap behavior

On first run (state file absent), every proposal in the response is "new" —
emitting all of them would flood the chat with historical state. So the
first run **records without emitting** (status_quo handshake). Subsequent
runs emit on differences only.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Status filter for content emission — these are the ECO-decided states.
# `eco_review` is excluded: AI shouldn't act on proposals ECO hasn't reviewed.
EMISSION_STATUSES = ("accepted", "changed", "declined")


@dataclass
class ProposalEvent:
    """One content event emitted to the fires log. AI consumes via Monitor."""
    instance_id: str
    loop_name: str
    proposal_id: str
    proposal_title: str
    status: str
    action_category: str | None
    eco_actor: str | None
    new_or_changed: str  # "new" | "status_changed"

    def to_log_line(self) -> str:
        """JSON line for ~/.empirica/loop_fires.log."""
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return json.dumps({
            "ts": now,
            "instance_id": self.instance_id,
            "loop": self.loop_name,
            "event_type": "proposal_event",
            "proposal_id": self.proposal_id,
            "proposal_title": self.proposal_title,
            "status": self.status,
            "action_category": self.action_category,
            "eco_actor": self.eco_actor,
            "change_kind": self.new_or_changed,
        })


def _state_path(instance_id: str, loop_name: str) -> Path:
    """`~/.empirica/loop_state/<inst>_<loop>.json` — per-loop seen state."""
    safe_inst = "".join(c if c.isalnum() or c in "-_" else "-" for c in instance_id)
    safe_loop = "".join(c if c.isalnum() or c in "-_" else "-" for c in loop_name)
    base = Path.home() / ".empirica" / "loop_state"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe_inst}_{safe_loop}.json"


def load_state(state_path: Path) -> dict:
    """Read the per-loop state file. Returns {} if absent or unreadable."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"content_poll state read failed at {state_path}: {e}")
        return {}


def save_state(state_path: Path, state: dict) -> None:
    """Atomic write — write to temp + rename, so a half-written file never
    poisons the next poll."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def fetch_cortex_inbox(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """GET /v1/orchestration/inbox?claude=<ai_id>&status=accepted,changed,declined.

    Returns the proposals[] list. Raises on network errors (caller decides
    whether to swallow — empty list is the conservative fallback so a
    transient Cortex outage doesn't emit spurious events).
    """
    params = urllib.parse.urlencode({
        "claude": ai_id,
        "status": ",".join(EMISSION_STATUSES),
        "related": "false",  # don't compute related_goals per proposal (faster)
    })
    url = f"{cortex_url.rstrip('/')}/v1/orchestration/inbox?{params}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        body = json.loads(raw) if raw else {}
    proposals = body.get("proposals", [])
    return proposals if isinstance(proposals, list) else []


def _proposal_status(p: dict) -> str:
    return str(p.get("status", "")).lower()


def _proposal_id(p: dict) -> str:
    return str(p.get("id") or "")


def diff_proposals(
    current: list[dict],
    last_seen: dict[str, str],
) -> list[tuple[dict, str]]:
    """Return (proposal, change_kind) for each new or status-changed item.

    change_kind ∈ {"new", "status_changed"}. Proposals whose ID + status are
    unchanged are filtered out — the AI already saw them.
    """
    out: list[tuple[dict, str]] = []
    for p in current:
        pid = _proposal_id(p)
        if not pid:
            continue
        status = _proposal_status(p)
        if status not in EMISSION_STATUSES:
            continue
        prior = last_seen.get(pid)
        if prior is None:
            out.append((p, "new"))
        elif prior != status:
            out.append((p, "status_changed"))
    return out


def build_event(p: dict, change_kind: str, instance_id: str, loop_name: str) -> ProposalEvent:
    """Compress a Cortex proposal payload into the emit shape."""
    eco_decision = p.get("eco_decision") or {}
    eco_actor = eco_decision.get("actor") if isinstance(eco_decision, dict) else None
    return ProposalEvent(
        instance_id=instance_id,
        loop_name=loop_name,
        proposal_id=_proposal_id(p),
        proposal_title=str(p.get("title", ""))[:200],
        status=_proposal_status(p),
        action_category=str(p.get("action_category") or "") or None,
        eco_actor=str(eco_actor) if eco_actor else None,
        new_or_changed=change_kind,
    )


def poll_and_diff(
    instance_id: str,
    loop_name: str,
    cortex_url: str,
    api_key: str,
    *,
    state_path: Path | None = None,
    fetch_fn=fetch_cortex_inbox,
) -> list[ProposalEvent]:
    """Pure-as-possible function for content-aware ticking.

    1. Fetch current ECO-decided proposals for `instance_id` from Cortex
    2. Load last-seen state from `state_path`
    3. Diff: emit events for new + status-changed proposals only
    4. Save current state back

    Bootstrap: on first run (state file absent), record current proposals
    as seen WITHOUT emitting — avoids flooding the chat with historical
    inbox content when David first enables a loop.

    `fetch_fn` is injectable for tests (default hits real Cortex via HTTP).

    Returns the list of events to emit. Caller writes them to fires log.
    """
    if state_path is None:
        state_path = _state_path(instance_id, loop_name)

    state = load_state(state_path)
    last_seen: dict[str, str] = state.get("proposals", {})
    last_seen_statuses = {pid: p.get("status", "") for pid, p in last_seen.items()
                          if isinstance(p, dict)} if isinstance(last_seen, dict) else {}

    try:
        current = fetch_fn(cortex_url, api_key, instance_id)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        # Transient network failure → emit nothing, don't update state.
        # Next tick retries. AFK guarantee preserved.
        logger.debug(f"content_poll fetch failed for {instance_id}/{loop_name}: {e}")
        return []

    bootstrap = not state  # first run: state file was absent

    diffs = diff_proposals(current, last_seen_statuses)
    events: list[ProposalEvent] = []
    if not bootstrap:
        events = [build_event(p, kind, instance_id, loop_name) for (p, kind) in diffs]

    # Always update state — even on bootstrap (records the status quo so the
    # NEXT tick has a baseline) and even when emitting (so the same proposal
    # doesn't re-emit on the next tick).
    new_proposals_map = {
        _proposal_id(p): {
            "status": _proposal_status(p),
            "seen_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        for p in current
        if _proposal_id(p)
    }
    save_state(state_path, {
        "last_poll_ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "proposals": new_proposals_map,
        "bootstrap_completed": True,
    })
    return events
