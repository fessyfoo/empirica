"""Tests for the mesh status health computation + curl-pid matching.

Surfaced by mesh-support prop_po4nyp3xzb: every cortex instance showed
curl=dead/health_color=red even while fires were actively flowing. Two
related bugs:

1. `_find_listener_pids` used `tags={ai_id}` (basename) as the curl
   needle, but post-3-form migration the listener subscribes with the
   canonical tag `tags=<org>.<tenant>.<ai_id>` — so curl_subprocess_pid
   was None for every instance.

2. `_compute_health` checked curl_subprocess_pid BEFORE the last_fire
   idle check — so even with fires flowing the fire-flow path was
   structurally unreachable.

Fix: match curl by either legacy basename needle OR canonical 3-form
suffix, with a delimiter check to prevent `tags=empirica` falsely
matching a cmdline carrying `tags=empirica-cortex`. Plus reorder
_compute_health so recent fires return green regardless of curl-pid.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from empirica.cli.command_handlers.mesh_commands import (
    ZOMBIE_THRESHOLD_SECONDS,
    _compute_health,
    _tag_matches,
)

# --- _tag_matches: delimiter-aware substring check ---


def test_tag_matches_exact_at_end():
    assert _tag_matches("curl -s ?tags=cortex", "tags=cortex") is True


def test_tag_matches_followed_by_ampersand():
    assert _tag_matches("?tags=cortex&since=...", "tags=cortex") is True


def test_tag_matches_followed_by_quote():
    assert _tag_matches('curl "?tags=cortex"', "tags=cortex") is True


def test_tag_matches_rejects_prefix_overlap():
    """tags=empirica must NOT match a cmdline carrying tags=empirica-cortex."""
    assert _tag_matches("?tags=empirica-cortex", "tags=empirica") is False


def test_tag_matches_dot_suffix_canonical():
    """The canonical-suffix match: cmdline carries the 3-form tag."""
    assert _tag_matches(
        "?tags=empirica.david.empirica-cortex", ".empirica-cortex"
    ) is True


def test_tag_matches_dot_suffix_rejects_prefix_overlap():
    """`.cortex` must NOT match `.empirica-cortex`."""
    assert _tag_matches("?tags=empirica.david.empirica-cortex", ".cortex") is False


def test_tag_matches_needle_absent_returns_false():
    assert _tag_matches("curl https://example.com", "tags=cortex") is False


# --- _compute_health: fire-flow is authoritative liveness signal ---


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _state(
    *,
    service_installed: bool = True,
    service_active: bool = True,
    listener_pid: int | None = 1234,
    curl_pid: int | None = 5678,
    last_fire: datetime | None = None,
    backoff_state: str | None = None,
) -> dict:
    return {
        "ai_id": "cortex",
        "service_installed": service_installed,
        "service_active": service_active,
        "listener_process_pid": listener_pid,
        "curl_subprocess_pid": curl_pid,
        "last_fire_at_utc": last_fire,
        "backoff_state": backoff_state,
    }


def test_recent_fires_with_curl_pid_missing_returns_green():
    """The core regression: pre-1.11.8 this was red ('curl subscription
    dead') even though fires were flowing. Fire-flow now wins.
    """
    recent = _now_utc() - timedelta(seconds=30)
    s = _state(curl_pid=None, last_fire=recent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "green"
    assert "last fire" in msg


def test_recent_fires_with_curl_pid_present_returns_green():
    recent = _now_utc() - timedelta(seconds=120)
    s = _state(curl_pid=5678, last_fire=recent)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "green"


def test_silent_fires_with_curl_dead_returns_red():
    """When fires HAVE gone silent past zombie threshold AND curl is
    not detected, red is the right call."""
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=None, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "curl subscription dead" in msg


def test_silent_fires_with_curl_alive_returns_red_zombie():
    """Curl alive but fires silent = real zombie."""
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=5678, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "zombie" in msg


def test_no_fires_yet_with_curl_alive_returns_yellow():
    s = _state(curl_pid=5678, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "yellow"
    assert "no fires recorded" in msg or "cold start" in msg


def test_no_fires_yet_with_curl_dead_returns_red():
    """Cold start + curl can't spawn = real outage."""
    s = _state(curl_pid=None, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "curl subscription dead" in msg


def test_rate_limit_backoff_with_no_fires_returns_yellow():
    s = _state(curl_pid=None, last_fire=None, backoff_state="rate_limit")
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "yellow"
    assert "rate" in msg.lower()


def test_service_not_installed_returns_yellow():
    s = _state(service_installed=False)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "yellow"


def test_service_inactive_returns_red():
    s = _state(service_active=False)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_listener_pid_missing_returns_red():
    s = _state(listener_pid=None)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_local_only_returns_green_when_cortex_unconfigured():
    """No cortex configured → curl checks don't apply."""
    s = _state(curl_pid=None, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=False)
    assert color == "green"
    assert "local-only" in msg
