"""Concept graph command parsers (experimental)."""


def add_concept_graph_parsers(subparsers):
    """Add concept graph command parsers"""

    # Concept graph build command
    concept_build_parser = subparsers.add_parser(
        "concept-build", help="Build concept graph from findings/unknowns (experimental)"
    )
    concept_build_parser.add_argument("--project-id", help="Project ID (auto-detects if not provided)")
    concept_build_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing concept data")
    concept_build_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Concept graph stats command
    concept_stats_parser = subparsers.add_parser("concept-stats", help="Show concept graph statistics (experimental)")
    concept_stats_parser.add_argument("--project-id", help="Project ID (auto-detects if not provided)")
    concept_stats_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Concept top command - show top concepts
    concept_top_parser = subparsers.add_parser("concept-top", help="Show top concepts by frequency (experimental)")
    concept_top_parser.add_argument("--project-id", help="Project ID (auto-detects if not provided)")
    concept_top_parser.add_argument("--limit", type=int, default=20, help="Maximum concepts to show (default: 20)")
    concept_top_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # Concept related command - find related concepts
    concept_related_parser = subparsers.add_parser(
        "concept-related", help="Find concepts related to a search term (experimental)"
    )
    concept_related_parser.add_argument("search_term", help="Term to search for related concepts")
    concept_related_parser.add_argument("--project-id", help="Project ID (auto-detects if not provided)")
    concept_related_parser.add_argument(
        "--limit", type=int, default=10, help="Maximum related concepts to show (default: 10)"
    )
    concept_related_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
