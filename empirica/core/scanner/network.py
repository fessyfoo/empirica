"""Network collector — listening + established sockets, metadata only.

Never inspects packets, headers, or payload. Emits the (pid, peer endpoint)
tuple per connection plus a summary of listening ports.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _conn_row(conn) -> dict[str, Any]:
    """Translate a psutil socket connection into the canonical row."""
    laddr = conn.laddr
    raddr = conn.raddr

    local_address = getattr(laddr, 'ip', None) if laddr else None
    local_port = getattr(laddr, 'port', None) if laddr else None
    peer_host = getattr(raddr, 'ip', None) if raddr else None
    peer_port = getattr(raddr, 'port', None) if raddr else None

    return {
        'pid': conn.pid,
        'family': str(conn.family).split('.')[-1] if conn.family else None,
        'type': str(conn.type).split('.')[-1] if conn.type else None,
        'local_address': local_address,
        'local_port': local_port,
        'peer_host': peer_host,
        'peer_port': peer_port,
        'status': conn.status,
    }


def collect_network(read_surface) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(payload, coverage)``.

    ``payload`` carries ``{'connections': [...], 'listening_ports': [...]}``
    as before; ``coverage`` reports how many sockets the OS handed back vs
    how many we successfully filtered. Permission denials (common when
    running unprivileged) are surfaced explicitly rather than silently.
    """
    coverage: dict[str, Any] = {
        'attempted': 0,
        'succeeded': 0,
        'skipped': 0,
        'skip_reasons': {},
    }

    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available; network collector returning empty")
        coverage['skip_reasons']['psutil_missing'] = 1
        return {'connections': [], 'listening_ports': []}, coverage

    try:
        # 'inet' captures TCP+UDP IPv4+IPv6; we never need unix sockets here.
        conns = list(psutil.net_connections(kind='inet'))
    except (psutil.AccessDenied, PermissionError) as exc:
        logger.info(f"net_connections requires elevated permissions: {exc}")
        coverage['skip_reasons']['access_denied'] = str(exc)
        return {'connections': [], 'listening_ports': []}, coverage
    except Exception as exc:
        logger.warning(f"net_connections failed: {exc}")
        coverage['skip_reasons']['net_connections_failed'] = str(exc)
        return {'connections': [], 'listening_ports': []}, coverage

    connections: list[dict[str, Any]] = []
    listening_ports: set[int] = set()

    for conn in conns:
        coverage['attempted'] += 1
        row = _conn_row(conn)
        if conn.status == 'LISTEN' and row['local_port']:
            listening_ports.add(row['local_port'])
        connections.append(read_surface.filter_dict('network', row))
        coverage['succeeded'] += 1

    coverage['ratio'] = (
        coverage['succeeded'] / coverage['attempted']
        if coverage['attempted'] else 1.0
    )
    return {
        'connections': connections,
        'listening_ports': sorted(listening_ports),
    }, coverage
