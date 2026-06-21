"""
Dispatch Bus — Typed cross-instance communication for Empirica.

A thin protocol layer on top of GitMessageStore that adds:
- Typed action dispatch (DispatchMessage with structured payload)
- Instance registry (~/.empirica/bus-instances.yaml)
- Capability matching (route to instances that can handle an action)
- Deadline enforcement (dispatches expire)
- Request/response correlation (via thread_id inheritance from GitMessageStore)

Use cases:
- Terminal Claude dispatches "schedule_cron" to web Cowork Claude
- Cowork dispatches "reply_to_this_github_issue" to terminal Claude
- Cortex dispatches "research_complete" back to the requester

Architecture:
    DispatchBus
      ├── GitMessageStore (transport/persistence)  ← existing
      ├── InstanceRegistry (YAML-backed, user-scoped)
      └── CapabilityMatcher (routes actions to capable instances)

The dispatch messages use channel "dispatch" by default and encode the typed
action in the message body as JSON. Regular GitMessageStore operations
(inbox, read, reply) all continue to work — dispatch is just a typed overlay.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]

    _YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

from .canonical.empirica_git.message_store import GitMessageStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Constants
# ---------------------------------------------------------------------------


class DispatchPriority(str, Enum):
    """Dispatch urgency."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DispatchStatus(str, Enum):
    """Lifecycle state of a dispatch."""

    PENDING = "pending"  # Sent, not yet acknowledged
    ACKNOWLEDGED = "acknowledged"  # Target confirmed receipt
    IN_PROGRESS = "in_progress"  # Target is working on it
    COMPLETED = "completed"  # Target finished successfully
    FAILED = "failed"  # Target failed to execute
    EXPIRED = "expired"  # Deadline passed without completion
    REJECTED = "rejected"  # Target refused (e.g., capability mismatch)


# Default channel for typed dispatches
DEFAULT_DISPATCH_CHANNEL = "dispatch"

# Default registry location (user-scoped)
DEFAULT_REGISTRY_PATH = Path.home() / ".empirica" / "bus-instances.yaml"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DispatchMessage:
    """
    A typed dispatch — an action request routed to another instance.

    Maps to a GitMessageStore message with:
    - channel = dispatch channel (default "dispatch")
    - subject = action name
    - body = JSON-encoded DispatchPayload
    - type = "request" (responses use "response")
    """

    action: str  # e.g., "schedule_cron", "send_email"
    from_instance: str  # instance_id of sender
    to_instance: str  # instance_id of target (or "*" for any-capable)
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None  # For request/response matching
    priority: DispatchPriority = DispatchPriority.NORMAL
    deadline: float | None = None  # Unix timestamp, None = no deadline
    required_capabilities: list[str] = field(default_factory=list)
    callback_channel: str | None = None  # Where to send results back
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message_body(self) -> str:
        """Serialize to JSON for GitMessageStore body field."""
        return json.dumps(
            {
                "action": self.action,
                "from_instance": self.from_instance,
                "to_instance": self.to_instance,
                "payload": self.payload,
                "correlation_id": self.correlation_id,
                "priority": self.priority.value if isinstance(self.priority, DispatchPriority) else self.priority,
                "deadline": self.deadline,
                "required_capabilities": self.required_capabilities,
                "callback_channel": self.callback_channel,
                "metadata": self.metadata,
            }
        )

    @classmethod
    def from_message_body(cls, body: str) -> DispatchMessage | None:
        """Parse from GitMessageStore body field."""
        try:
            data = json.loads(body)
            return cls(
                action=data.get("action", ""),
                from_instance=data.get("from_instance", ""),
                to_instance=data.get("to_instance", ""),
                payload=data.get("payload", {}),
                correlation_id=data.get("correlation_id"),
                priority=DispatchPriority(data.get("priority", "normal")),
                deadline=data.get("deadline"),
                required_capabilities=data.get("required_capabilities", []),
                callback_channel=data.get("callback_channel"),
                metadata=data.get("metadata", {}),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse dispatch message body: {e}")
            return None

    def is_expired(self) -> bool:
        """Check if dispatch deadline has passed."""
        if self.deadline is None:
            return False
        return time.time() > self.deadline


@dataclass
class DispatchResult:
    """
    Result of a dispatch sent back to the originator.
    """

    correlation_id: str  # Matches DispatchMessage.correlation_id
    status: DispatchStatus
    from_instance: str  # Who completed it
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message_body(self) -> str:
        return json.dumps(
            {
                "correlation_id": self.correlation_id,
                "status": self.status.value if isinstance(self.status, DispatchStatus) else self.status,
                "from_instance": self.from_instance,
                "payload": self.payload,
                "error": self.error,
                "duration_ms": self.duration_ms,
                "metadata": self.metadata,
            }
        )

    @classmethod
    def from_message_body(cls, body: str) -> DispatchResult | None:
        try:
            data = json.loads(body)
            return cls(
                correlation_id=data.get("correlation_id", ""),
                status=DispatchStatus(data.get("status", "pending")),
                from_instance=data.get("from_instance", ""),
                payload=data.get("payload", {}),
                error=data.get("error"),
                duration_ms=data.get("duration_ms"),
                metadata=data.get("metadata", {}),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse dispatch result body: {e}")
            return None


@dataclass
class InstanceInfo:
    """
    Metadata about a registered Claude instance.
    """

    instance_id: str  # e.g., "terminal-claude-1", "cowork-web-1"
    instance_type: str  # "claude-code-cli", "cowork-web", "desktop-app", "cortex-server"
    capabilities: list[str] = field(default_factory=list)
    subscribes: list[str] = field(default_factory=list)  # Channels to poll
    machine: str | None = None
    registered_at: float | None = None
    last_seen: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def has_all_capabilities(self, required: list[str]) -> bool:
        return all(c in self.capabilities for c in required)


# ---------------------------------------------------------------------------
# Instance Registry
# ---------------------------------------------------------------------------


class InstanceRegistry:
    """
    YAML-backed registry of known Claude instances.

    Stored at ~/.empirica/bus-instances.yaml by default.
    Each instance declares capabilities and subscribed channels.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_REGISTRY_PATH
        self._instances: dict[str, InstanceInfo] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if not _YAML_AVAILABLE or yaml is None:
            logger.warning("PyYAML not available — instance registry disabled")
            return
        if not self.path.exists():
            return
        try:
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
            instances_data = data.get("instances", {})
            for inst_id, inst_data in instances_data.items():
                if not isinstance(inst_data, dict):
                    continue
                self._instances[inst_id] = InstanceInfo(
                    instance_id=inst_id,
                    instance_type=inst_data.get("type", "unknown"),
                    capabilities=inst_data.get("capabilities", []),
                    subscribes=inst_data.get("subscribes", []),
                    machine=inst_data.get("machine"),
                    registered_at=inst_data.get("registered_at"),
                    last_seen=inst_data.get("last_seen"),
                    metadata=inst_data.get("metadata", {}),
                )
        except Exception as e:
            logger.warning(f"Failed to load instance registry: {e}")

    def _save(self) -> None:
        """Persist registry to disk."""
        if not _YAML_AVAILABLE or yaml is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "instances": {
                inst_id: {
                    "type": info.instance_type,
                    "capabilities": info.capabilities,
                    "subscribes": info.subscribes,
                    "machine": info.machine,
                    "registered_at": info.registered_at,
                    "last_seen": info.last_seen,
                    "metadata": info.metadata,
                }
                for inst_id, info in self._instances.items()
            }
        }
        try:
            with open(self.path, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
        except OSError as e:
            logger.warning(f"Failed to save instance registry: {e}")

    def register(
        self,
        instance_id: str,
        instance_type: str,
        capabilities: list[str],
        subscribes: list[str] | None = None,
        machine: str | None = None,
        metadata: dict | None = None,
    ) -> InstanceInfo:
        """Register or update an instance."""
        info = InstanceInfo(
            instance_id=instance_id,
            instance_type=instance_type,
            capabilities=capabilities,
            subscribes=subscribes or [],
            machine=machine,
            registered_at=time.time()
            if instance_id not in self._instances
            else self._instances[instance_id].registered_at,
            last_seen=time.time(),
            metadata=metadata or {},
        )
        self._instances[instance_id] = info
        self._save()
        return info

    def unregister(self, instance_id: str) -> bool:
        """Remove an instance from the registry."""
        if instance_id in self._instances:
            del self._instances[instance_id]
            self._save()
            return True
        return False

    def get(self, instance_id: str) -> InstanceInfo | None:
        return self._instances.get(instance_id)

    def list_all(self) -> list[InstanceInfo]:
        return list(self._instances.values())

    def find_by_capability(self, capability: str) -> list[InstanceInfo]:
        """Find all instances that have a given capability."""
        return [info for info in self._instances.values() if info.has_capability(capability)]

    def find_by_capabilities(self, capabilities: list[str]) -> list[InstanceInfo]:
        """Find all instances that have ALL the given capabilities."""
        return [info for info in self._instances.values() if info.has_all_capabilities(capabilities)]

    def touch(self, instance_id: str) -> None:
        """Update last_seen timestamp for an instance."""
        if instance_id in self._instances:
            self._instances[instance_id].last_seen = time.time()
            self._save()


# ---------------------------------------------------------------------------
# DispatchBus
# ---------------------------------------------------------------------------


class DispatchBus:
    """
    Typed dispatch layer over GitMessageStore.

    Responsibilities:
    - Send typed DispatchMessages (wraps GitMessageStore.send_message)
    - Route dispatches by capability (via InstanceRegistry)
    - Poll for incoming dispatches (wraps GitMessageStore.get_inbox)
    - Send DispatchResults back to originators
    - Enforce deadlines
    """

    def __init__(
        self,
        instance_id: str,
        message_store: GitMessageStore | None = None,
        registry: InstanceRegistry | None = None,
        default_channel: str = DEFAULT_DISPATCH_CHANNEL,
    ):
        """
        Args:
            instance_id: This instance's identifier (e.g., "terminal-claude-1")
            message_store: GitMessageStore instance (creates one if None)
            registry: InstanceRegistry (creates one if None)
            default_channel: Channel name for dispatch messages
        """
        self.instance_id = instance_id
        self.store = message_store or GitMessageStore()
        self.registry = registry or InstanceRegistry()
        self.default_channel = default_channel

    # --- Registration ---

    def register_self(
        self,
        instance_type: str,
        capabilities: list[str],
        subscribes: list[str] | None = None,
        metadata: dict | None = None,
    ) -> InstanceInfo:
        """Register this instance in the shared registry."""
        return self.registry.register(
            instance_id=self.instance_id,
            instance_type=instance_type,
            capabilities=capabilities,
            subscribes=subscribes,
            metadata=metadata,
        )

    # --- Dispatch ---

    def dispatch(
        self,
        to_instance: str,
        action: str,
        payload: dict | None = None,
        priority: DispatchPriority = DispatchPriority.NORMAL,
        deadline_seconds: int | None = None,
        required_capabilities: list[str] | None = None,
        callback_channel: str | None = None,
        ttl: int = 86400,
        metadata: dict | None = None,
    ) -> str | None:
        """
        Send a typed dispatch to another instance.

        Args:
            to_instance: Target instance_id, or "*" for capability-routed
            action: Action name (e.g., "schedule_cron")
            payload: Action-specific parameters
            priority: Dispatch priority
            deadline_seconds: How long before the dispatch expires
            required_capabilities: If to_instance is "*", match these capabilities
            callback_channel: Where to send the result (defaults to dispatch channel)
            ttl: Message TTL in GitMessageStore
            metadata: Additional metadata

        Returns:
            correlation_id for tracking the dispatch, or None on failure
        """
        # Resolve target via capability if wildcard
        if to_instance == "*" and required_capabilities:
            matches = self.registry.find_by_capabilities(required_capabilities)
            if not matches:
                logger.warning(f"No instances match capabilities: {required_capabilities}")
                return None
            # Pick the first match (could be load-balanced in future)
            to_instance = matches[0].instance_id

        correlation_id = f"{self.instance_id}-{int(time.time() * 1000)}-{action}"
        deadline = time.time() + deadline_seconds if deadline_seconds else None

        dispatch_msg = DispatchMessage(
            action=action,
            from_instance=self.instance_id,
            to_instance=to_instance,
            payload=payload or {},
            correlation_id=correlation_id,
            priority=priority,
            deadline=deadline,
            required_capabilities=required_capabilities or [],
            callback_channel=callback_channel or self.default_channel,
            metadata=metadata or {},
        )

        # Send via GitMessageStore
        msg_id = self.store.send_message(
            from_ai_id=self.instance_id,
            to_ai_id=to_instance,
            channel=self.default_channel,
            subject=f"dispatch:{action}",
            body=dispatch_msg.to_message_body(),
            message_type="request",
            priority=priority.value if isinstance(priority, DispatchPriority) else priority,
            ttl=ttl,
            metadata={
                "dispatch_action": action,
                "correlation_id": correlation_id,
                **(metadata or {}),
            },
        )

        if not msg_id:
            logger.warning(f"Failed to send dispatch {action} to {to_instance}")
            return None

        logger.info(f"Dispatched {action} → {to_instance} (correlation: {correlation_id[:16]}...)")
        return correlation_id

    # --- Result reporting ---

    def send_result(
        self,
        original_message_id: str,
        original_channel: str,
        correlation_id: str,
        status: DispatchStatus,
        payload: dict | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> str | None:
        """
        Send a DispatchResult back to the originator.

        Uses GitMessageStore.reply() to thread the response to the original.
        """
        result = DispatchResult(
            correlation_id=correlation_id,
            status=status,
            from_instance=self.instance_id,
            payload=payload or {},
            error=error,
            duration_ms=duration_ms,
        )

        return self.store.reply(
            original_message_id=original_message_id,
            original_channel=original_channel,
            from_ai_id=self.instance_id,
            body=result.to_message_body(),
            message_type="response",
            metadata={"correlation_id": correlation_id, "dispatch_status": status.value},
        )

    # --- Inbox polling ---

    def poll_inbox(
        self,
        channel: str | None = None,
        status: str = "unread",
        include_expired: bool = False,
        limit: int = 50,
    ) -> list[DispatchMessage]:
        """
        Poll for incoming dispatches addressed to this instance.

        Returns only valid DispatchMessages (messages that parse correctly
        and are not expired). Raw GitMessageStore messages are available
        via self.store.get_inbox() if needed.
        """
        raw_msgs = self.store.get_inbox(
            ai_id=self.instance_id,
            channel=channel or self.default_channel,
            status=status,
            include_expired=include_expired,
            limit=limit,
        )

        dispatches = []
        for msg in raw_msgs:
            body = msg.get("body", "")
            dispatch = DispatchMessage.from_message_body(body)
            if dispatch is None:
                continue
            if dispatch.is_expired() and not include_expired:
                continue
            # Attach the original message_id and channel for reply tracking
            dispatch.metadata["_message_id"] = msg.get("message_id")
            dispatch.metadata["_channel"] = msg.get("channel")
            dispatches.append(dispatch)

        return dispatches

    def poll_results(
        self,
        channel: str | None = None,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[DispatchResult]:
        """
        Poll for DispatchResults (responses to dispatches we sent).

        If correlation_id is given, only return results matching that ID.
        """
        raw_msgs = self.store.get_inbox(
            ai_id=self.instance_id,
            channel=channel or self.default_channel,
            status="all",
            include_expired=False,
            limit=limit,
        )

        results = []
        for msg in raw_msgs:
            if msg.get("type") != "response":
                continue
            body = msg.get("body", "")
            result = DispatchResult.from_message_body(body)
            if result is None:
                continue
            if correlation_id and result.correlation_id != correlation_id:
                continue
            results.append(result)

        return results

    def wait_for_result(
        self,
        correlation_id: str,
        timeout_seconds: int = 60,
        poll_interval: float = 2.0,
    ) -> DispatchResult | None:
        """
        Block until a DispatchResult with the given correlation_id arrives,
        or timeout. Polls self.store at poll_interval.
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            results = self.poll_results(correlation_id=correlation_id)
            if results:
                return results[0]
            time.sleep(poll_interval)
        return None

    # --- Handler pattern ---

    def handle_dispatch(
        self,
        dispatch: DispatchMessage,
        handler: Callable[[DispatchMessage], tuple[DispatchStatus, dict, str | None]],
    ) -> str | None:
        """
        Execute a handler for a received dispatch and send back the result.

        Handler signature: (dispatch) -> (status, payload, error_or_none)

        Returns the reply message_id, or None on failure.
        """
        original_message_id = dispatch.metadata.get("_message_id")
        original_channel = dispatch.metadata.get("_channel")

        if not original_message_id or not original_channel:
            logger.warning("Cannot send result: missing original message_id/channel in dispatch metadata")
            return None

        start = time.time()
        try:
            status, payload, error = handler(dispatch)
        except Exception as e:
            logger.exception(f"Handler failed for action {dispatch.action}")
            status = DispatchStatus.FAILED
            payload = {}
            error = str(e)

        duration_ms = int((time.time() - start) * 1000)

        return self.send_result(
            original_message_id=original_message_id,
            original_channel=original_channel,
            correlation_id=dispatch.correlation_id or "",
            status=status,
            payload=payload,
            error=error,
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_bus: DispatchBus | None = None


def get_global_bus() -> DispatchBus | None:
    """Get the module-level global bus, if set."""
    return _global_bus


def set_global_bus(bus: DispatchBus) -> None:
    """Set the module-level global bus."""
    global _global_bus
    _global_bus = bus
