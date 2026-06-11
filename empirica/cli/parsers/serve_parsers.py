"""
Serve Parsers - Local daemon for Chrome extension integration

Commands:
- serve: Start FastAPI daemon on localhost for extension communication
"""

import os


def add_serve_parsers(subparsers):
    """Add serve command parser."""

    serve_parser = subparsers.add_parser(
        'serve',
        help='Start local daemon for Chrome extension integration',
        description='Launch a FastAPI server on localhost that the Empirica Chrome '
                    'extension uses to import artifacts, sync profiles, and query status. '
                    'Runs on http://localhost:8000 by default.',
    )
    serve_parser.add_argument(
        '--port', type=int,
        default=int(os.getenv('EMPIRICA_SERVE_PORT', '8000')),
        help='Port to listen on (default: 8000, or EMPIRICA_SERVE_PORT env; '
             'the explicit flag wins over the env var)'
    )
    serve_parser.add_argument(
        '--host', default='127.0.0.1',
        help='Host to bind to (default: 127.0.0.1, use 0.0.0.0 for network access)'
    )
    serve_parser.add_argument(
        '--reload', action='store_true',
        help='Enable auto-reload on code changes (development only)'
    )
