"""Loop scheduler backends — systemd-user (Linux/WSL2), launchd (macOS Phase 2).

Decouples loop cadence from Claude Code's in-session CronCreate so loops can be
truly paused (`systemctl --user stop`) without depending on Claude cooperation,
while preserving wake-from-idle via the Monitor tool bridge (see goal f718156c
decision log).

Public API:
  - SystemdLoopScheduler — Phase 1a backend (Linux, WSL2-with-systemd)
  - is_systemd_available() — capability probe

Future:
  - LaunchdLoopScheduler (macOS Phase 2)
  - TaskSchedulerLoopScheduler (Windows native Phase 3 — most users on WSL2)
"""

from .launchd import LaunchdLoopScheduler, LaunchdUnavailable, is_launchd_available
from .listener import run_listener
from .persistent_listener import (
    ListenerServiceUnavailable,
    ListenerStatus,
    PersistentListenerService,
    install_listener_for,
    is_listener_running,
    listener_status_for,
    uninstall_listener_for,
)
from .systemd import (
    SystemdLoopScheduler,
    SystemdUnavailable,
    is_systemd_available,
    list_active_loops_for_instance,
)


class LoopSchedulerUnavailable(RuntimeError):
    """No supported scheduler backend on this host."""


def get_loop_scheduler(empirica_bin: str = "empirica"):
    """Pick the OS scheduler backend for this host.

    Priority: systemd-user (Linux + WSL2) > launchd (macOS) > error.
    The check order matters because some macOS dev setups can have
    systemctl installed (homebrew packages, container tools) — we
    prefer the native OS scheduler for each platform.

    Returns a scheduler with the canonical API: enable/disable/status/
    list_enabled/tick. Handlers should depend on the interface, not the
    concrete class.

    Raises LoopSchedulerUnavailable on Windows-native (Phase 3) and
    other platforms without a supported scheduler.
    """
    import sys

    if sys.platform == "darwin":
        if is_launchd_available():
            return LaunchdLoopScheduler(empirica_bin)
    elif is_systemd_available():
        return SystemdLoopScheduler(empirica_bin)
    raise LoopSchedulerUnavailable(
        "No supported scheduler on this host. Linux/WSL2 needs systemd-user "
        "(check `systemctl --user is-system-running`). macOS needs launchctl "
        "(should be present by default). Windows-native is Phase 3 — use WSL2 "
        "in the meantime."
    )


__all__ = [
    "LaunchdLoopScheduler",
    "LaunchdUnavailable",
    "ListenerServiceUnavailable",
    "ListenerStatus",
    "LoopSchedulerUnavailable",
    "PersistentListenerService",
    "SystemdLoopScheduler",
    "SystemdUnavailable",
    "get_loop_scheduler",
    "install_listener_for",
    "is_launchd_available",
    "is_listener_running",
    "is_systemd_available",
    "list_active_loops_for_instance",
    "listener_status_for",
    "run_listener",
    "uninstall_listener_for",
]
