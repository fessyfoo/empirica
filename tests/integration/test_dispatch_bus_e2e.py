"""
End-to-end integration tests for the dispatch bus.

Spawns two simulated Claude instances (one terminal, one cowork) using the
DispatchBus directly and verifies the full request → response loop works
through GitMessageStore as transport.

This is the closest we can get to testing real cross-instance dispatch without
actually running multiple subprocesses or remote machines.
"""

import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from empirica.core.canonical.empirica_git.message_store import GitMessageStore
from empirica.core.dispatch_bus import (
    DispatchBus,
    DispatchPriority,
    DispatchStatus,
    InstanceRegistry,
)


@pytest.fixture
def git_repo():
    """Create a temporary git repo and initialize it with a commit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, check=True)
        (Path(tmpdir) / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmpdir, check=True)
        yield tmpdir


@pytest.fixture
def shared_registry(tmp_path):
    """Temp registry that both instances share."""
    return InstanceRegistry(path=tmp_path / "bus-instances.yaml")


@pytest.fixture
def terminal_bus(git_repo, shared_registry):
    """Simulated terminal Claude bus."""
    store = GitMessageStore(workspace_root=git_repo)
    bus = DispatchBus(
        instance_id="terminal-claude-1",
        message_store=store,
        registry=shared_registry,
    )
    bus.register_self(
        instance_type="claude-code-cli",
        capabilities=["codebase", "git", "shell"],
        subscribes=["dispatch.terminal", "dispatch"],
    )
    return bus


@pytest.fixture
def cowork_bus(git_repo, shared_registry):
    """Simulated web Cowork Claude bus."""
    store = GitMessageStore(workspace_root=git_repo)
    bus = DispatchBus(
        instance_id="cowork-web-1",
        message_store=store,
        registry=shared_registry,
    )
    bus.register_self(
        instance_type="cowork-web",
        capabilities=["browser", "gmail", "schedule"],
        subscribes=["dispatch.cowork", "dispatch"],
    )
    return bus


# ---------------------------------------------------------------------------
# Direct dispatch tests
# ---------------------------------------------------------------------------


class TestDirectDispatch:
    def test_terminal_dispatches_to_cowork(self, terminal_bus, cowork_bus):
        """Terminal sends a dispatch, cowork polls and receives it."""
        correlation_id = terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="schedule_cron",
            payload={"schedule": "0 9 * * *", "command": "run X"},
        )
        assert correlation_id is not None

        # Cowork polls
        dispatches = cowork_bus.poll_inbox()
        assert len(dispatches) == 1
        d = dispatches[0]
        assert d.action == "schedule_cron"
        assert d.from_instance == "terminal-claude-1"
        assert d.payload["schedule"] == "0 9 * * *"
        assert d.correlation_id == correlation_id

    def test_dispatch_with_priority(self, terminal_bus, cowork_bus):
        terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="urgent_task",
            priority=DispatchPriority.URGENT,
        )
        dispatches = cowork_bus.poll_inbox()
        assert dispatches[0].priority == DispatchPriority.URGENT

    def test_dispatch_with_deadline(self, terminal_bus, cowork_bus):
        terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="deadlined_task",
            deadline_seconds=3600,
        )
        dispatches = cowork_bus.poll_inbox()
        assert dispatches[0].deadline is not None
        assert not dispatches[0].is_expired()


# ---------------------------------------------------------------------------
# Capability routing
# ---------------------------------------------------------------------------


class TestCapabilityRouting:
    def test_route_by_capability(self, terminal_bus, cowork_bus):
        """Terminal dispatches to '*' with 'gmail' capability — cowork should match."""
        correlation_id = terminal_bus.dispatch(
            to_instance="*",
            action="send_email",
            required_capabilities=["gmail"],
            payload={"to": "user@example.com"},
        )
        assert correlation_id is not None

        dispatches = cowork_bus.poll_inbox()
        assert len(dispatches) == 1
        assert dispatches[0].action == "send_email"

    def test_no_match_returns_none(self, terminal_bus):
        """Dispatching to '*' with unmatched capability returns None."""
        result = terminal_bus.dispatch(
            to_instance="*",
            action="impossible",
            required_capabilities=["nonexistent_capability"],
        )
        assert result is None


# ---------------------------------------------------------------------------
# Request → response cycle
# ---------------------------------------------------------------------------


class TestRequestResponse:
    def test_full_request_response_cycle(self, terminal_bus, cowork_bus):
        """Terminal dispatches, cowork handles, terminal receives result."""
        # Terminal dispatches
        correlation_id = terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="ping",
            payload={"data": "hello"},
        )

        # Cowork polls and handles
        dispatches = cowork_bus.poll_inbox()
        assert len(dispatches) == 1

        def handler(d):
            return DispatchStatus.COMPLETED, {"echo": d.payload["data"]}, None

        result_id = cowork_bus.handle_dispatch(dispatches[0], handler)
        assert result_id is not None

        # Terminal polls for results
        results = terminal_bus.poll_results(correlation_id=correlation_id)
        assert len(results) == 1
        result = results[0]
        assert result.status == DispatchStatus.COMPLETED
        assert result.payload["echo"] == "hello"
        assert result.correlation_id == correlation_id
        assert result.from_instance == "cowork-web-1"

    def test_handler_failure_returns_failed_status(self, terminal_bus, cowork_bus):
        correlation_id = terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="boom",
        )
        dispatches = cowork_bus.poll_inbox()

        def failing_handler(d):
            raise RuntimeError("simulated failure")

        cowork_bus.handle_dispatch(dispatches[0], failing_handler)
        results = terminal_bus.poll_results(correlation_id=correlation_id)
        assert len(results) == 1
        assert results[0].status == DispatchStatus.FAILED
        assert "simulated failure" in (results[0].error or "")


# ---------------------------------------------------------------------------
# Wait-for-result blocking
# ---------------------------------------------------------------------------


class TestWaitForResult:
    def test_wait_for_result_succeeds(self, terminal_bus, cowork_bus):
        """Terminal blocks waiting for cowork's response, gets it before timeout."""
        # Cowork worker thread that processes dispatches
        threading.Event()
        worker_done = threading.Event()

        def worker():
            time.sleep(0.5)  # Let terminal start waiting first
            dispatches = cowork_bus.poll_inbox()
            if dispatches:
                cowork_bus.handle_dispatch(
                    dispatches[0],
                    lambda d: (DispatchStatus.COMPLETED, {"answer": 42}, None),
                )
            worker_done.set()

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        correlation_id = terminal_bus.dispatch(
            to_instance="cowork-web-1",
            action="compute",
        )
        result = terminal_bus.wait_for_result(
            correlation_id=correlation_id,
            timeout_seconds=10,
            poll_interval=0.5,
        )
        worker_thread.join(timeout=2)

        assert result is not None
        assert result.status == DispatchStatus.COMPLETED
        assert result.payload["answer"] == 42

    def test_wait_for_result_times_out(self, terminal_bus):
        """No worker — should time out and return None."""
        result = terminal_bus.wait_for_result(
            correlation_id="nonexistent-corr-id",
            timeout_seconds=1,
            poll_interval=0.3,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Registry persistence across bus instances
# ---------------------------------------------------------------------------


class TestRegistryPersistence:
    def test_registry_shared_between_buses(self, terminal_bus, cowork_bus, shared_registry):
        """Both buses see each other in the shared registry."""
        all_instances = shared_registry.list_all()
        instance_ids = {i.instance_id for i in all_instances}
        assert "terminal-claude-1" in instance_ids
        assert "cowork-web-1" in instance_ids

    def test_find_by_capability_cross_instance(self, terminal_bus, cowork_bus, shared_registry):
        """Terminal can discover cowork's capabilities."""
        gmail_instances = shared_registry.find_by_capability("gmail")
        assert len(gmail_instances) == 1
        assert gmail_instances[0].instance_id == "cowork-web-1"

        codebase_instances = shared_registry.find_by_capability("codebase")
        assert len(codebase_instances) == 1
        assert codebase_instances[0].instance_id == "terminal-claude-1"
