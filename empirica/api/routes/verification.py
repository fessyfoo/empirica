"""Crypto verification endpoints"""

import logging

from flask import Blueprint, jsonify, request

bp = Blueprint("verification", __name__)
logger = logging.getLogger(__name__)


@bp.route("/checkpoints/<session_id>/<phase>/<int:round>/verify", methods=["GET"])
def verify_checkpoint(session_id: str, phase: str, round: int):
    """
    Verify cryptographic signature of a checkpoint.

    Returns verification status, signer identity, and content hash.
    """
    public_key = request.args.get("public_key")
    return jsonify(
        {
            "ok": True,
            "checkpoint_id": f"{session_id}/{phase}/{round}",
            "git_note_sha": "pending",
            "signature_verified": False,
            "signed_by": "unknown",
            "signature_date": "unknown",
            "public_key": public_key or "unknown",
            "content_hash": "sha256:pending",
            "verification_method": "ed25519_signature",
        }
    )


@bp.route("/sessions/<session_id>/signatures", methods=["GET"])
def list_session_signatures(session_id: str):
    """
    List all verified signatures for a session.

    Shows verification status of all PREFLIGHT, CHECK, and POSTFLIGHT checkpoints.
    """
    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "signatures": [
                {
                    "phase": "PREFLIGHT",
                    "round": 1,
                    "timestamp": "pending",
                    "git_note_sha": "pending",
                    "verified": False,
                    "signed_by": "unknown",
                    "public_key": "pending",
                }
            ],
            "all_verified": False,
            "verification_status": "not_implemented",
        }
    )
