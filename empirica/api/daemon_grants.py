"""Daemon credential grant lifecycle — UI-prompted token flow.

Implements the OAuth-Device-Code-shaped consent model for the
extension's full-key auto-connect (goal 167fc8d4 / prop_b4si26t7c5):

  1. Extension calls POST /api/v1/credentials/grant/request.
     Daemon mints (device_code, user_code) + writes a pending grant
     record to disk + emits a stdout line so the user sees what to
     approve.

  2. User runs `empirica daemon-grant <user_code>` (or `daemon-deny`).
     CLI verb finds the pending record by user_code, marks it
     approved/denied + binds the full credentials snapshot if
     approved.

  3. Extension polls POST /api/v1/credentials/grant/poll {device_code}.
     Daemon reads the on-disk record. Returns:
       - pending  → still waiting
       - approved → returns the full credentials ONCE + deletes the
         record (one-shot)
       - denied   → returns denied + deletes the record
       - expired  → returns expired + deletes the record

The CLI and the daemon don't talk directly — they coordinate via
the on-disk record. Both processes read/write `~/.empirica/daemon_grants/`
atomically.

Security properties:
  - device_code is 32 random bytes (hex) — only the requesting
    extension instance ever sees it after the request response;
    poll requires producing it.
  - user_code is 8 chars from an unambiguous alphabet (no I/1/O/0)
    — short enough for a human to type, large enough to be
    practically unguessable in the 5-min window.
  - Approval requires local filesystem write under `~/.empirica/`
    — defense-in-depth against a remote attacker who somehow
    reaches the loopback API: they need shell access too.
  - One-shot delivery — the record is deleted on the FIRST
    successful poll. Replay returns `not_found`.
  - 5-min default TTL — past `expires_at`, the next poll returns
    `expired` and deletes the record.

See PROPOSAL prop_b4si26t7c5 for the original ask + David's call
on the UI-prompted-token consent model (2026-06-07 session).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 300  # 5 min — matches OAuth Device Code recommendation
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/1/O/0
USER_CODE_LENGTH = 8
DEVICE_CODE_BYTES = 32  # 64 hex chars
POLL_INTERVAL_SEC = 2


def grants_dir() -> Path:
    """Per-user directory where pending grants live."""
    return Path.home() / ".empirica" / "daemon_grants"


# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class GrantRecord:
    """On-disk representation of a single grant lifecycle.

    Status transitions:
      pending → approved (terminal — delivered on next poll, then deleted)
      pending → denied   (terminal — surfaced on next poll, then deleted)
      pending → expired  (computed at poll time from expires_at — not persisted)
    """

    device_code: str
    user_code: str
    requesting_app: str
    created_at: float
    expires_at: float
    status: str  # 'pending' | 'approved' | 'denied'
    approved_at: float | None = None
    denied_at: float | None = None
    # Credentials snapshot bound at approve time. None until approved.
    granted_credentials: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> GrantRecord:
        return cls(
            device_code=data["device_code"],
            user_code=data["user_code"],
            requesting_app=data.get("requesting_app", "unknown"),
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
            status=data.get("status", "pending"),
            approved_at=data.get("approved_at"),
            denied_at=data.get("denied_at"),
            granted_credentials=data.get("granted_credentials"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ── Codes ──────────────────────────────────────────────────────────────


def mint_device_code() -> str:
    """Cryptographically random opaque code, never seen by the user.

    Only the extension instance that called request/ ever knows this
    value (it's returned in the request response). Producing it on
    poll/ is what proves the request came from the same instance.
    """
    return secrets.token_hex(DEVICE_CODE_BYTES)


def mint_user_code() -> str:
    """Short human-typable code for the CLI approval verb.

    Format: XXXX-XXXX (e.g. AB23-CDEF). The dash is cosmetic — the
    matcher strips it before comparing.
    """
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(value: str) -> str:
    """Strip whitespace/dashes + upper-case for matching."""
    return value.replace("-", "").replace(" ", "").upper()


# ── Persistence ────────────────────────────────────────────────────────


def _record_path(device_code: str) -> Path:
    return grants_dir() / f"{device_code}.json"


def write_record(record: GrantRecord) -> Path:
    """Atomic write — tempfile in the same dir + os.replace.

    Same pattern as registry.save_registry: never partial-corrupts the
    file even on crash mid-write.
    """
    d = grants_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = _record_path(record.device_code)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".grant-", suffix=".json.tmp", dir=str(d))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, sort_keys=True)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def read_record(device_code: str) -> GrantRecord | None:
    p = _record_path(device_code)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return GrantRecord.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"daemon_grants: failed to read {p}: {e}")
        return None


def delete_record(device_code: str) -> bool:
    p = _record_path(device_code)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning(f"daemon_grants: failed to delete {p}: {e}")
        return False


def list_records() -> list[GrantRecord]:
    """All current grants on disk. Pending + terminal-but-not-yet-polled."""
    d = grants_dir()
    if not d.exists():
        return []
    records: list[GrantRecord] = []
    for p in sorted(d.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                records.append(GrantRecord.from_dict(json.load(f)))
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            # Tolerate corrupt entries — don't break listing
            continue
    return records


def find_by_user_code(user_code: str) -> GrantRecord | None:
    """Lookup by user_code (normalised). Returns the first match.

    Collisions are vanishingly unlikely with USER_CODE_ALPHABET^8 and
    a 5-min window, but if one exists we surface the oldest (creation
    order). Caller-side ambiguity is the right cue to deny + ask the
    user to re-request.
    """
    needle = normalize_user_code(user_code)
    for record in sorted(list_records(), key=lambda r: r.created_at):
        if normalize_user_code(record.user_code) == needle:
            return record
    return None


# ── Lifecycle ──────────────────────────────────────────────────────────


def create_grant(
    requesting_app: str = "unknown",
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: float | None = None,
) -> GrantRecord:
    """Mint a fresh grant record + persist + return it.

    Daemon route handler calls this on POST /grant/request.
    """
    t = now if now is not None else time.time()
    record = GrantRecord(
        device_code=mint_device_code(),
        user_code=mint_user_code(),
        requesting_app=requesting_app,
        created_at=t,
        expires_at=t + ttl_sec,
        status="pending",
    )
    write_record(record)
    return record


def approve_grant(
    user_code: str,
    credentials: dict,
    now: float | None = None,
) -> GrantRecord | None:
    """CLI calls this on `empirica daemon-grant <user_code>`.

    Looks up by user_code, snapshots the current credentials, marks
    approved. Returns the updated record, or None if the user_code
    doesn't match a pending grant (already-approved/denied/expired
    all yield None here — caller decides what to surface).
    """
    record = find_by_user_code(user_code)
    if record is None or record.status != "pending":
        return None
    t = now if now is not None else time.time()
    if t > record.expires_at:
        return None
    record.status = "approved"
    record.approved_at = t
    record.granted_credentials = credentials
    write_record(record)
    return record


def deny_grant(user_code: str, now: float | None = None) -> GrantRecord | None:
    """CLI calls this on `empirica daemon-deny <user_code>`. Mirrors approve."""
    record = find_by_user_code(user_code)
    if record is None or record.status != "pending":
        return None
    t = now if now is not None else time.time()
    record.status = "denied"
    record.denied_at = t
    write_record(record)
    return record


def poll_grant(device_code: str, now: float | None = None) -> dict:
    """Daemon route handler calls this on POST /grant/poll.

    Returns one of:
      {"status": "not_found"}
      {"status": "pending", "expires_at": <epoch>}
      {"status": "expired"}                            (record deleted)
      {"status": "denied"}                             (record deleted)
      {"status": "approved", "credentials": {...}}     (record deleted)

    Approved + denied + expired all consume the record — one-shot
    delivery. Replays yield `not_found`.
    """
    record = read_record(device_code)
    if record is None:
        return {"status": "not_found"}
    t = now if now is not None else time.time()
    if t > record.expires_at and record.status == "pending":
        delete_record(device_code)
        return {"status": "expired"}
    if record.status == "approved":
        creds = record.granted_credentials or {}
        delete_record(device_code)
        return {"status": "approved", "credentials": creds}
    if record.status == "denied":
        delete_record(device_code)
        return {"status": "denied"}
    return {"status": "pending", "expires_at": record.expires_at}


def reap_expired(now: float | None = None) -> int:
    """Best-effort sweep — delete all records past expires_at that the
    extension never polled. Returns count removed.

    Not strictly required for correctness (poll_grant detects expiry on
    its own), but keeps the directory tidy. Safe to run on daemon
    startup or periodically.
    """
    t = now if now is not None else time.time()
    removed = 0
    for record in list_records():
        if t > record.expires_at:
            if delete_record(record.device_code):
                removed += 1
    return removed
