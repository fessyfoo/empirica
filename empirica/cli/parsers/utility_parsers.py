"""Utility command parsers."""


def add_utility_parsers(subparsers):
    """Add utility command parsers"""
    # Goal analysis command
    goal_parser = subparsers.add_parser("goal-analysis", help="Analyze goal feasibility")
    goal_parser.add_argument("goal", help="Goal to analyze")
    goal_parser.add_argument("--context", help="JSON context data")
    goal_parser.add_argument("--verbose", action="store_true", help="Show detailed analysis")

    # Token savings commands
    log_token_saving_parser = subparsers.add_parser("log-token-saving", help="Log a token saving event")
    log_token_saving_parser.add_argument("--session-id", required=True, help="Session ID")
    log_token_saving_parser.add_argument(
        "--type",
        required=True,
        choices=["doc_awareness", "finding_reuse", "mistake_prevention", "handoff_efficiency"],
        help="Type of token saving",
    )
    log_token_saving_parser.add_argument("--tokens", type=int, required=True, help="Tokens saved")
    log_token_saving_parser.add_argument("--evidence", required=True, help="What was avoided/reused")
    log_token_saving_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    efficiency_report_parser = subparsers.add_parser("efficiency-report", help="Show token efficiency report")
    efficiency_report_parser.add_argument("--session-id", required=True, help="Session ID")
    efficiency_report_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Qdrant maintenance commands
    qdrant_cleanup_parser = subparsers.add_parser(
        "qdrant-cleanup", help="Remove empty Qdrant collections to reduce resource usage"
    )
    qdrant_cleanup_parser.add_argument(
        "--execute", action="store_true", default=False, help="Actually delete empty collections (default: dry-run)"
    )
    qdrant_cleanup_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    qdrant_status_parser = subparsers.add_parser("qdrant-status", help="Show Qdrant collection inventory and stats")
    qdrant_status_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
