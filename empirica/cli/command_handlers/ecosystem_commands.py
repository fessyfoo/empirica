"""
Ecosystem Commands - Dependency analysis and impact checking

Split from project_commands.py for maintainability.
"""

import json
import logging

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _eco_handle_validate(graph, output_format):
    """Handle --validate sub-command: check manifest integrity."""
    issues = graph.validate()
    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": len(issues) == 0,
                    "issues": issues,
                    "issue_count": len(issues),
                }
            )
        )
    else:
        if issues:
            print(f"Found {len(issues)} issue(s):\n")
            for i in issues:
                print(f"  WARNING: {i}")
        else:
            print("Ecosystem manifest is valid. No issues found.")
    return 0 if not issues else 1


def _eco_handle_file(graph, check_file, output_format):
    """Handle --file sub-command: impact analysis for a specific file."""
    impact = graph.impact_of(check_file)
    if output_format == "json":
        print(json.dumps({"ok": True, **impact}, indent=2))
    else:
        if impact["project"]:
            print(f"File: {check_file}")
            print(f"Project: {impact['project']}")
            print(f"Exports affected: {'Yes' if impact['exports_affected'] else 'No'}")
            print(f"Downstream impact: {impact['downstream_count']} project(s)")
            if impact["downstream"]:
                for d in impact["downstream"]:
                    print(f"  -> {d}")
        else:
            print(f"File '{check_file}' does not belong to any known project.")
    return 0


def _eco_handle_project(graph, check_project, output_format):
    """Handle --project sub-command: show upstream/downstream for a project."""
    if check_project not in graph.projects:
        msg = f"Project '{check_project}' not found in manifest."
        if output_format == "json":
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg)
        return 1

    upstream = sorted(graph.upstream(check_project))
    downstream = sorted(graph.downstream(check_project))
    config = graph.projects[check_project]

    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": True,
                    "project": check_project,
                    "role": config.get("role"),
                    "type": config.get("type"),
                    "description": config.get("description"),
                    "upstream": upstream,
                    "upstream_count": len(upstream),
                    "downstream": downstream,
                    "downstream_count": len(downstream),
                },
                indent=2,
            )
        )
    else:
        print(f"Project: {check_project}")
        print(f"  Role: {config.get('role', 'unknown')}")
        print(f"  Type: {config.get('type', 'unknown')}")
        print(f"  Description: {config.get('description', '')}")
        print()
        print(f"Upstream ({len(upstream)}):")
        for u in upstream:
            print(f"  <- {u}")
        if not upstream:
            print("  (none - root project)")
        print()
        print(f"Downstream ({len(downstream)}):")
        for d in downstream:
            print(f"  -> {d}")
        if not downstream:
            print("  (none - leaf project)")
    return 0


def _eco_handle_filter(graph, filter_key, filter_value, output_format):
    """Handle --role or --tag sub-command: filter projects."""
    if filter_key == "role":
        projects = graph.by_role(filter_value)
    else:
        projects = graph.by_tag(filter_value)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": True,
                    filter_key: filter_value,
                    "count": len(projects),
                    "projects": projects,
                },
                indent=2,
            )
        )
    else:
        print(f"Projects with {filter_key} '{filter_value}' ({len(projects)}):")
        for p in sorted(projects):
            desc = graph.projects[p].get("description", "")
            print(f"  {p}: {desc}")
    return 0


def _eco_handle_summary(graph, output_format):
    """Handle default sub-command: full ecosystem summary dashboard."""
    summary = graph.summary()

    if output_format == "json":
        print(json.dumps({"ok": True, **summary}, indent=2))
        return 0

    print("=" * 64)
    print("  Empirica Ecosystem Overview")
    print("=" * 64)
    print()
    print(f"  Total Projects: {summary['total_projects']}")
    print(f"  Dependency Edges: {summary['dependency_edges']}")
    print()

    print("  By Role:")
    for role, count in sorted(summary["by_role"].items()):
        print(f"    {role:20s} {count}")
    print()

    print("  By Type:")
    for ptype, count in sorted(summary["by_type"].items()):
        print(f"    {ptype:20s} {count}")
    print()

    print(f"  Root Projects ({len(summary['root_projects'])}):")
    for p in summary["root_projects"]:
        print(f"    {p}")
    print()

    print(f"  Leaf Projects ({len(summary['leaf_projects'])}):")
    for p in summary["leaf_projects"]:
        print(f"    {p}")
    print()

    print("  Dependency Tree (from empirica):")
    _print_dep_tree(graph, "empirica", indent=4)
    print()

    return 0


def handle_ecosystem_check_command(args):
    """Handle ecosystem-check command - analyze dependencies and impact from ecosystem.yaml"""
    try:
        from empirica.core.ecosystem import load_ecosystem

        manifest_path = getattr(args, "manifest", None)
        output_format = getattr(args, "output", "human")

        try:
            graph = load_ecosystem(manifest_path)
        except FileNotFoundError as e:
            if output_format == "json":
                print(json.dumps({"ok": False, "error": str(e)}))
            else:
                print(f"Error: {e}")
            return 1

        if getattr(args, "validate", False):
            return _eco_handle_validate(graph, output_format)

        check_file = getattr(args, "file", None)
        if check_file:
            return _eco_handle_file(graph, check_file, output_format)

        check_project = getattr(args, "project", None)
        if check_project:
            return _eco_handle_project(graph, check_project, output_format)

        check_role = getattr(args, "role", None)
        if check_role:
            return _eco_handle_filter(graph, "role", check_role, output_format)

        check_tag = getattr(args, "tag", None)
        if check_tag:
            return _eco_handle_filter(graph, "tag", check_tag, output_format)

        return _eco_handle_summary(graph, output_format)

    except Exception as e:
        handle_cli_error(e, "Ecosystem check", getattr(args, "verbose", False))
        return 1


def _print_dep_tree(graph, project, indent=0, visited=None):
    """Print dependency tree for a project (downstream)."""
    if visited is None:
        visited = set()
    if project in visited:
        print(" " * indent + f"{project} (circular)")
        return
    visited.add(project)
    print(" " * indent + project)
    direct = sorted(graph.downstream(project, transitive=False))
    for dep in direct:
        _print_dep_tree(graph, dep, indent + 2, visited.copy())
