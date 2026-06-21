"""
Bus Parsers - CLI argument parsers for dispatch bus commands.
"""

from empirica.cli.command_handlers.bus_commands import (
    handle_bus_dispatch_command,
    handle_bus_instances_command,
    handle_bus_register_command,
    handle_bus_status_command,
    handle_bus_subscribe_command,
)


def add_bus_parsers(subparsers):
    """Register dispatch bus command parsers."""

    # bus-register
    reg_parser = subparsers.add_parser(
        "bus-register", help="Register this Claude instance in the shared dispatch bus registry"
    )
    reg_parser.add_argument("--instance-id", required=True, help="Unique instance ID (e.g., terminal-claude-1)")
    reg_parser.add_argument(
        "--type", required=True, help="Instance type (claude-code-cli, cowork-web, desktop-app, cortex-server)"
    )
    reg_parser.add_argument("--capabilities", help="Comma-separated capabilities (e.g., codebase,git,shell)")
    reg_parser.add_argument("--subscribes", help="Comma-separated channels to subscribe to")
    reg_parser.add_argument("--output", choices=["human", "json"], default="json")
    reg_parser.set_defaults(func=handle_bus_register_command)

    # bus-dispatch
    disp_parser = subparsers.add_parser("bus-dispatch", help="Send a typed dispatch action to another instance")
    disp_parser.add_argument("--from", dest="from_instance", help="Sender instance ID (default: claude-code)")
    disp_parser.add_argument(
        "--to", dest="to_instance", required=True, help='Target instance ID, or "*" for capability-routed'
    )
    disp_parser.add_argument("--action", required=True, help="Action name (e.g., schedule_cron, send_email)")
    disp_parser.add_argument("--payload", help="JSON payload string for the action")
    disp_parser.add_argument("--priority", choices=["low", "normal", "high", "urgent"], default="normal")
    disp_parser.add_argument("--deadline", type=int, help="Dispatch deadline in seconds from now")
    disp_parser.add_argument("--required-capabilities", help='Comma-separated capabilities (for --to "*" routing)')
    disp_parser.add_argument("--callback-channel", help="Channel for the response (default: dispatch)")
    disp_parser.add_argument("--ttl", type=int, default=86400, help="Git message TTL seconds (default: 24h)")
    disp_parser.add_argument("--wait", action="store_true", help="Block until the dispatch completes or times out")
    disp_parser.add_argument("--wait-timeout", type=int, default=60, help="Max seconds to wait if --wait (default: 60)")
    disp_parser.add_argument("--output", choices=["human", "json"], default="json")
    disp_parser.set_defaults(func=handle_bus_dispatch_command)

    # bus-subscribe
    sub_parser = subparsers.add_parser("bus-subscribe", help="Subscribe to a dispatch channel (blocking)")
    sub_parser.add_argument("--instance-id", required=True, help="This instance ID")
    sub_parser.add_argument("--channel", default="dispatch", help="Channel to subscribe to (default: dispatch)")
    sub_parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls (default: 2.0)")
    sub_parser.add_argument("--limit", type=int, default=50, help="Max dispatches per poll (default: 50)")
    sub_parser.add_argument("--output", choices=["human", "json"], default="json")
    sub_parser.set_defaults(func=handle_bus_subscribe_command)

    # bus-instances
    inst_parser = subparsers.add_parser("bus-instances", help="List all registered bus instances")
    inst_parser.add_argument("--capability", help="Filter instances that have this capability")
    inst_parser.add_argument("--output", choices=["human", "json"], default="json")
    inst_parser.set_defaults(func=handle_bus_instances_command)

    # bus-status
    status_parser = subparsers.add_parser("bus-status", help="Show an instance's registry state and inbox summary")
    status_parser.add_argument("--instance-id", required=True, help="Instance ID to query")
    status_parser.add_argument("--output", choices=["human", "json"], default="json")
    status_parser.set_defaults(func=handle_bus_status_command)
