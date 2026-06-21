"""
Bus Commands - CLI handlers for DispatchBus operations.

Typed cross-instance dispatch on top of GitMessageStore. Commands:
- bus-register: Register this instance in ~/.empirica/bus-instances.yaml
- bus-dispatch: Send a typed action dispatch to another instance
- bus-subscribe: Poll for incoming dispatches (blocking)
- bus-instances: List registered instances
- bus-status: Show this instance's registry state and inbox summary
"""

import json
import logging
import sys
import time
from typing import Any

from empirica.core.dispatch_bus import (
    DispatchBus,
    DispatchPriority,
    InstanceRegistry,
)

logger = logging.getLogger(__name__)


def _parse_payload(raw: str | None) -> dict:
    """Parse --payload as JSON, return empty dict if None."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid --payload JSON: {e}")
        return {}


def _output(result: dict, output_format: str = "json") -> int:
    """Write result to stdout in requested format."""
    if output_format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        # Human-readable
        if result.get("ok") is False:
            print(f"❌ {result.get('error', 'failed')}")
            return 1
        for k, v in result.items():
            if k == "ok":
                continue
            if isinstance(v, (dict, list)):
                print(f"{k}:")
                print(json.dumps(v, indent=2, default=str))
            else:
                print(f"{k}: {v}")
    return 0 if result.get("ok", True) else 1


def handle_bus_register_command(args) -> int:
    """
    Register this instance in the shared bus registry.

    Example:
        empirica bus-register --instance-id terminal-claude-1 \\
            --type claude-code-cli \\
            --capabilities codebase,git,shell \\
            --subscribes dispatch.terminal,findings.global
    """
    instance_id = args.instance_id
    instance_type = args.type
    capabilities = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
    subscribes = [s.strip() for s in (args.subscribes or "").split(",") if s.strip()]

    if not instance_id or not instance_type:
        return _output({"ok": False, "error": "--instance-id and --type required"}, args.output)

    registry = InstanceRegistry()
    info = registry.register(
        instance_id=instance_id,
        instance_type=instance_type,
        capabilities=capabilities,
        subscribes=subscribes,
    )
    return _output(
        {
            "ok": True,
            "instance_id": info.instance_id,
            "type": info.instance_type,
            "capabilities": info.capabilities,
            "subscribes": info.subscribes,
            "registered_at": info.registered_at,
        },
        args.output,
    )


def handle_bus_dispatch_command(args) -> int:
    """
    Send a typed dispatch to another instance.

    Example:
        empirica bus-dispatch --from terminal-1 --to cowork-1 \\
            --action schedule_cron \\
            --payload '{"schedule": "0 9 * * *", "command": "run X"}' \\
            --deadline 3600 \\
            --wait
    """
    from_instance = args.from_instance or "claude-code"
    to_instance = args.to_instance
    action = args.action

    if not to_instance or not action:
        return _output({"ok": False, "error": "--to and --action required"}, args.output)

    payload = _parse_payload(args.payload)
    required_caps = [c.strip() for c in (args.required_capabilities or "").split(",") if c.strip()]

    bus = DispatchBus(instance_id=from_instance)
    correlation_id = bus.dispatch(
        to_instance=to_instance,
        action=action,
        payload=payload,
        priority=DispatchPriority(args.priority),
        deadline_seconds=args.deadline,
        required_capabilities=required_caps or None,
        callback_channel=args.callback_channel,
        ttl=args.ttl,
    )

    if not correlation_id:
        return _output({"ok": False, "error": "dispatch failed (see logs)"}, args.output)

    result = {
        "ok": True,
        "correlation_id": correlation_id,
        "to_instance": to_instance,
        "action": action,
    }

    if args.wait:
        timeout = args.wait_timeout
        dispatch_result = bus.wait_for_result(
            correlation_id=correlation_id,
            timeout_seconds=timeout,
            poll_interval=2.0,
        )
        if dispatch_result:
            result["result"] = {
                "status": dispatch_result.status.value,
                "payload": dispatch_result.payload,
                "error": dispatch_result.error,
                "duration_ms": dispatch_result.duration_ms,
                "from_instance": dispatch_result.from_instance,
            }
        else:
            result["result"] = {"status": "timeout", "timeout_seconds": timeout}

    return _output(result, args.output)


def handle_bus_subscribe_command(args) -> int:
    """
    Subscribe to the dispatch channel and print incoming dispatches.

    Blocking. Ctrl+C to stop.

    Example:
        empirica bus-subscribe --instance-id terminal-1 --channel dispatch
    """
    instance_id = args.instance_id
    channel = args.channel or "dispatch"

    if not instance_id:
        return _output({"ok": False, "error": "--instance-id required"}, args.output)

    bus = DispatchBus(instance_id=instance_id)

    if args.output == "json":
        print(json.dumps({"ok": True, "subscribing": channel, "instance_id": instance_id}))
    else:
        print(f"Subscribing to {channel} as {instance_id}...", file=sys.stderr)
        print("Press Ctrl+C to stop.", file=sys.stderr)

    try:
        while True:
            dispatches = bus.poll_inbox(channel=channel, limit=args.limit or 50)
            for d in dispatches:
                output = {
                    "action": d.action,
                    "from_instance": d.from_instance,
                    "payload": d.payload,
                    "correlation_id": d.correlation_id,
                    "priority": d.priority.value if hasattr(d.priority, "value") else d.priority,
                    "message_id": d.metadata.get("_message_id"),
                    "received_at": time.time(),
                }
                if args.output == "json":
                    print(json.dumps(output, default=str))
                    sys.stdout.flush()
                else:
                    print(f"\n📨 [{d.action}] from {d.from_instance}")
                    print(f"   correlation_id: {d.correlation_id}")
                    print(f"   payload: {json.dumps(d.payload, indent=4)}")
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        if args.output != "json":
            print("\nStopped.", file=sys.stderr)
        return 0


def handle_bus_instances_command(args) -> int:
    """
    List all registered instances.

    Example:
        empirica bus-instances
        empirica bus-instances --capability gmail
    """
    registry = InstanceRegistry()

    if args.capability:
        instances = registry.find_by_capability(args.capability)
    else:
        instances = registry.list_all()

    if args.output == "json":
        data = [
            {
                "instance_id": i.instance_id,
                "type": i.instance_type,
                "capabilities": i.capabilities,
                "subscribes": i.subscribes,
                "machine": i.machine,
                "registered_at": i.registered_at,
                "last_seen": i.last_seen,
            }
            for i in instances
        ]
        return _output({"ok": True, "instances": data, "count": len(data)}, args.output)
    else:
        print(f"Registered Instances ({len(instances)}):")
        for i in instances:
            print(f"\n  {i.instance_id}")
            print(f"    type: {i.instance_type}")
            print(f"    capabilities: {', '.join(i.capabilities)}")
            if i.subscribes:
                print(f"    subscribes: {', '.join(i.subscribes)}")
            if i.machine:
                print(f"    machine: {i.machine}")
        return 0


def handle_bus_status_command(args) -> int:
    """
    Show this instance's registry state and inbox summary.

    Example:
        empirica bus-status --instance-id terminal-1
    """
    instance_id = args.instance_id
    if not instance_id:
        return _output({"ok": False, "error": "--instance-id required"}, args.output)

    registry = InstanceRegistry()
    info = registry.get(instance_id)

    bus = DispatchBus(instance_id=instance_id, registry=registry)
    pending_dispatches = bus.poll_inbox(status="unread")
    pending_results = bus.poll_results()

    result: dict[str, Any] = {
        "ok": True,
        "instance_id": instance_id,
        "registered": info is not None,
    }
    if info:
        result["type"] = info.instance_type
        result["capabilities"] = info.capabilities
        result["subscribes"] = info.subscribes
        result["last_seen"] = info.last_seen
    result["pending_dispatches"] = len(pending_dispatches)
    result["pending_results"] = len(pending_results)
    result["total_instances_known"] = len(registry.list_all())

    return _output(result, args.output)
