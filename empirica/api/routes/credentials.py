"""Credential grant endpoints — extracted from serve_app.py.

The UI-prompted-token flow lives here so `create_serve_app()` doesn't
swell past the complexity limit. Same routing surface
(`/api/v1/credentials/grant/...`); no behavioral change vs the inline
version.

See `empirica.api.daemon_grants` for the state lifecycle.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ─────────────────────────────────────────────────────────────


class CredentialGrantRequest(BaseModel):
    """Extension asks the daemon for a credential grant. The actual
    credentials are returned later (after explicit user approval via
    `empirica daemon-grant <user_code>`) — never on this call."""

    requesting_app: str = "extension"


class CredentialGrantRequestResponse(BaseModel):
    """Response to /grant/request — the codes the extension will use
    to drive the consent UX. Never carries credentials."""

    ok: bool
    device_code: str | None = None  # secret, only this extension sees it
    user_code: str | None = None  # human-typable, shown to user
    expires_at: float | None = None  # epoch
    poll_interval_sec: int | None = None
    error: str | None = None


class CredentialGrantPollRequest(BaseModel):
    device_code: str


class CredentialGrantPollResponse(BaseModel):
    """Response to /grant/poll. The full credentials snapshot is
    delivered exactly once, on the first poll after approval. After
    that the device_code resolves to `not_found`."""

    status: str  # 'pending' | 'approved' | 'denied' | 'expired' | 'not_found'
    credentials: dict | None = None
    expires_at: float | None = None


# ── Routes ─────────────────────────────────────────────────────────────


@router.post(
    "/api/v1/credentials/grant/request",
    response_model=CredentialGrantRequestResponse,
)
async def credentials_grant_request(
    req: CredentialGrantRequest,
) -> CredentialGrantRequestResponse:
    """Begin the UI-prompted full-credential grant flow.

    Mints a device_code / user_code pair, persists a pending grant
    record under ~/.empirica/daemon_grants/, and prints an approval
    hint to the daemon's own stdout so the user running `empirica
    serve` sees what's pending. The daemon DOES NOT return the
    actual credentials here — they're delivered later on /poll after
    the user explicitly approves via the CLI verb.

    Replaces the never-shipped 'return full key on GET' pattern the
    original prop_b4si26t7c5 floated. David's call (2026-06-07) was
    the consent model: ambient credentials over the loopback API are
    wrong; explicit out-of-band approval is right."""
    try:
        from empirica.api import daemon_grants

        record = daemon_grants.create_grant(
            requesting_app=req.requesting_app or "extension",
        )
        # Print to the daemon's stdout — the operator running
        # `empirica serve` sees this and knows what to type. Not a
        # logger.warning so it stays visible regardless of log level.
        print(
            f"\n⚠️  Credential grant requested by "
            f"{record.requesting_app!r}. "
            f"User code: {record.user_code}\n"
            f"   Approve: empirica daemon-grant {record.user_code}\n"
            f"   Deny:    empirica daemon-deny  {record.user_code}\n",
            flush=True,
        )
        return CredentialGrantRequestResponse(
            ok=True,
            device_code=record.device_code,
            user_code=record.user_code,
            expires_at=record.expires_at,
            poll_interval_sec=daemon_grants.POLL_INTERVAL_SEC,
        )
    except Exception as e:
        logger.error(f"credentials_grant_request failed: {e}", exc_info=True)
        return CredentialGrantRequestResponse(ok=False, error=str(e))


@router.post(
    "/api/v1/credentials/grant/poll",
    response_model=CredentialGrantPollResponse,
)
async def credentials_grant_poll(
    req: CredentialGrantPollRequest,
) -> CredentialGrantPollResponse:
    """Poll a pending grant. Producing the device_code is the proof
    that this poll comes from the extension instance that requested
    the grant. Returns the full credentials snapshot exactly once (on
    the first poll after the user approves); thereafter the
    device_code resolves to not_found (one-shot delivery)."""
    try:
        from empirica.api import daemon_grants

        result = daemon_grants.poll_grant(req.device_code)
        return CredentialGrantPollResponse(
            status=result["status"],
            credentials=result.get("credentials"),
            expires_at=result.get("expires_at"),
        )
    except Exception as e:
        logger.error(f"credentials_grant_poll failed: {e}", exc_info=True)
        return CredentialGrantPollResponse(status="error")
