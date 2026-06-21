"""
Empirica Identity Module - Cryptographic Trust Layer (Phase 2 / EEP-1)

Implements AI Identity with Ed25519 keypairs and cryptographic signatures
for establishing trust and provenance in AI-generated content.

Core Components:
- AIIdentity: Keypair management and identity storage
- Signature: EEP-1 signature generation and verification
- IdentityManager: Multi-identity management utilities

EEP-1 Signature Payload:
{
    "content_hash": "SHA-256 of output",
    "creator_id": "public_key_hex",
    "timestamp": "ISO 8601",
    "epistemic_state_final": {"KNOW": 0.92, "UNCERTAINTY": 0.05, ...},
    "cascade_trace_hash": "SHA-256 of git log",
    "metadata_sources": ["url1", "file2"],
    "model_id": "gemini-2.5-flash"
}
+ Ed25519 signature
"""

from .ai_identity import AIIdentity, IdentityManager
from .signature import create_eep1_payload, sign_assessment, verify_eep1_payload, verify_signature

__all__ = [
    "AIIdentity",
    "IdentityManager",
    "create_eep1_payload",
    "sign_assessment",
    "verify_eep1_payload",
    "verify_signature",
]
