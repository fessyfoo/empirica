"""
Serve command handler — starts FastAPI daemon for Chrome extension.
"""

import logging

logger = logging.getLogger(__name__)


def handle_serve_command(args):
    """Start the Empirica serve daemon."""
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    reload = getattr(args, "reload", False)

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install 'empirica[api]'")
        return 1

    # Fail-closed: never expose the entity-mint endpoint unauthenticated. A
    # non-loopback bind requires a configured service-token set.
    from empirica.api.entity_mint_auth import assert_bind_safe

    try:
        assert_bind_safe(host)
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1

    print(f"Starting Empirica serve daemon on http://{host}:{port}")
    print(f"  Health:  http://{host}:{port}/api/v1/health")
    print(f"  Import:  POST http://{host}:{port}/api/v1/artifacts/import")
    print(f"  Status:  GET  http://{host}:{port}/api/v1/profile/status")
    print(f"  Sync:    POST http://{host}:{port}/api/v1/profile/sync")
    print()

    uvicorn.run(
        "empirica.api.serve_app:create_serve_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
        log_level="info",
    )

    return 0
