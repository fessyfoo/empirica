#!/usr/bin/env python3
"""Chrome MCP health check for long-lived Empirica sessions.

Detects dead Chrome MCP connections by checking:
1. Native host process alive
2. Bridge socket exists and not stale
3. Debug log bridge poll pattern (empty polls = disconnected)

Exit codes:
  0 = healthy
  1 = degraded (warnings)
  2 = dead (needs restart)

Output: JSON with status, details, and recovery instructions.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def check_native_host_process():
    """Check if chrome-native-host process is running."""
    try:
        result = subprocess.run(["pgrep", "-f", "chrome-native-host"], capture_output=True, text=True, timeout=5)
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if not pids:
            return {"status": "dead", "detail": "No chrome-native-host process found"}
        if len(pids) > 1:
            return {
                "status": "degraded",
                "detail": f"Multiple native host processes: {pids} (stale processes likely)",
                "pids": pids,
            }
        return {"status": "healthy", "detail": f"Native host running (PID {pids[0]})", "pids": pids}
    except Exception as e:
        return {"status": "unknown", "detail": f"Process check failed: {e}"}


def check_bridge_sockets():
    """Check Unix sockets in the bridge directory."""
    username = os.environ.get("USER", "unknown")
    bridge_dir = Path(f"/tmp/claude-mcp-browser-bridge-{username}")

    if not bridge_dir.exists():
        return {"status": "dead", "detail": "Bridge directory does not exist"}

    sockets = list(bridge_dir.glob("*.sock"))
    if not sockets:
        return {"status": "dead", "detail": "No bridge sockets found"}

    # Check if socket PIDs are alive
    live_sockets = []
    stale_sockets = []
    for sock in sockets:
        pid = sock.stem
        try:
            os.kill(int(pid), 0)
            live_sockets.append(str(sock))
        except (ProcessLookupError, ValueError):
            stale_sockets.append(str(sock))

    if stale_sockets and not live_sockets:
        return {
            "status": "dead",
            "detail": f"All sockets stale: {stale_sockets}",
            "live_sockets": live_sockets,
            "stale_sockets": stale_sockets,
        }
    elif stale_sockets:
        return {
            "status": "degraded",
            "detail": f"Stale sockets present: {stale_sockets}",
            "live_sockets": live_sockets,
            "stale_sockets": stale_sockets,
        }
    else:
        return {
            "status": "healthy",
            "detail": f"Live sockets: {live_sockets}",
            "live_sockets": live_sockets,
            "stale_sockets": stale_sockets,
        }


def check_bridge_polls():
    """Check debug log for empty bridge poll pattern."""
    debug_dir = Path.home() / ".claude" / "debug"
    if not debug_dir.exists():
        return {"status": "unknown", "detail": "No debug directory"}

    # Find most recent debug file
    debug_files = sorted(debug_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not debug_files:
        return {"status": "unknown", "detail": "No debug log files"}

    latest = debug_files[0]

    # Read last 50KB of file to find bridge poll pattern
    try:
        file_size = latest.stat().st_size
        read_size = min(file_size, 50000)
        with open(latest, "rb") as f:
            f.seek(max(0, file_size - read_size))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"status": "unknown", "detail": f"Failed to read debug log: {e}"}

    # Find the highest consecutive empty poll count
    poll_pattern = re.findall(r"no work, (\d+) consecutive empty polls", tail)
    if not poll_pattern:
        # No bridge polling found — might mean chrome not enabled
        # Check if there's a successful chrome connection
        if "claude-in-chrome" in tail and "Successfully connected" in tail:
            return {"status": "healthy", "detail": "Chrome MCP connected (no bridge polling)"}
        return {"status": "unknown", "detail": "No bridge poll data in recent log"}

    max_empty = max(int(p) for p in poll_pattern)

    # Check for STDIO pipe death
    pipe_broken = "Broken pipe" in tail and "claude-in-chrome" in tail

    if pipe_broken:
        return {
            "status": "dead",
            "detail": f"STDIO pipe broken. Bridge polling with {max_empty} consecutive empty polls.",
            "empty_polls": max_empty,
            "pipe_broken": True,
        }

    if max_empty > 1000:
        return {
            "status": "dead",
            "detail": f"{max_empty} consecutive empty bridge polls — connection likely dead",
            "empty_polls": max_empty,
        }
    elif max_empty > 100:
        return {
            "status": "degraded",
            "detail": f"{max_empty} consecutive empty bridge polls — connection may be degraded",
            "empty_polls": max_empty,
        }
    else:
        return {
            "status": "healthy",
            "detail": f"Bridge polling active ({max_empty} empty polls — normal range)",
            "empty_polls": max_empty,
        }


def check_mcp_tools_registered():
    """Check if Chrome MCP tools are in the session (heuristic: look for recent tool calls)."""
    debug_dir = Path.home() / ".claude" / "debug"
    if not debug_dir.exists():
        return {"status": "unknown", "detail": "No debug directory"}

    debug_files = sorted(debug_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not debug_files:
        return {"status": "unknown", "detail": "No debug log files"}

    latest = debug_files[0]

    try:
        file_size = latest.stat().st_size
        read_size = min(file_size, 100000)
        with open(latest, "rb") as f:
            f.seek(max(0, file_size - read_size))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"status": "unknown", "detail": f"Failed to read debug log: {e}"}

    # Check for MCP server startup
    if 'MCP server "claude-in-chrome": Successfully connected' in tail:
        # Find the most recent connection and check if there's a subsequent drop
        lines = tail.split("\n")
        last_connect = None
        last_drop = None
        for line in lines:
            if "claude-in-chrome" in line and "Successfully connected" in line:
                last_connect = line
            if "claude-in-chrome" in line and ("dropped" in line.lower() or "broken pipe" in line.lower()):
                last_drop = line

        if last_drop and last_connect:
            # Both exist — check which is more recent (by position in file)
            connect_pos = tail.rfind("Successfully connected")
            drop_pos = tail.rfind("dropped") if "dropped" in tail else tail.rfind("Broken pipe")
            if drop_pos > connect_pos:
                return {"status": "dead", "detail": "Chrome MCP connected then dropped (not recovered)"}

        return {"status": "healthy", "detail": "Chrome MCP server connected"}

    return {"status": "unknown", "detail": "No Chrome MCP connection found in recent logs"}


def generate_recovery_instructions(checks):
    """Generate recovery steps based on check results."""
    instructions = []

    # Check for stale sockets
    socket_check = checks.get("sockets", {})
    if socket_check.get("stale_sockets"):
        instructions.append(f"Remove stale sockets: rm {' '.join(socket_check['stale_sockets'])}")

    # Check for multiple native hosts
    process_check = checks.get("process", {})
    if process_check.get("pids") and len(process_check.get("pids", [])) > 1:
        instructions.append(f"Kill stale native hosts: kill {' '.join(process_check['pids'][:-1])}")

    # Universal recovery
    if any(c.get("status") == "dead" for c in checks.values()):
        instructions.extend(
            [
                "Full recovery (requires session restart):",
                "  1. pkill -f 'chrome-native-host'",
                f"  2. rm -rf /tmp/claude-mcp-browser-bridge-{os.environ.get('USER', 'unknown')}/",
                "  3. Exit Claude Code and restart: claude --chrome",
                "  4. Run: empirica project-bootstrap --output json",
                "",
                "Known issue: anthropics/claude-code#25956",
                "Root cause: Cloud WebSocket bridge drops in long-lived sessions",
            ]
        )

    return instructions


def main():
    checks = {
        "process": check_native_host_process(),
        "sockets": check_bridge_sockets(),
        "bridge_polls": check_bridge_polls(),
        "mcp_server": check_mcp_tools_registered(),
    }

    # Determine overall status
    statuses = [c["status"] for c in checks.values()]
    if "dead" in statuses:
        overall = "dead"
        exit_code = 2
    elif "degraded" in statuses:
        overall = "degraded"
        exit_code = 1
    elif "unknown" in statuses and "healthy" not in statuses:
        overall = "unknown"
        exit_code = 1
    else:
        overall = "healthy"
        exit_code = 0

    recovery = generate_recovery_instructions(checks) if overall != "healthy" else []

    output = {
        "status": overall,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": checks,
        "recovery": recovery,
    }

    print(json.dumps(output, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
