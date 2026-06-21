"""Context markdown formatter for AI prompt injection"""


def _format_project_header(breadcrumbs: dict) -> list:
    project = breadcrumbs.get("project", {})
    return [
        f"# Project: {project.get('name', 'Unknown')}",
        f"> {project.get('description', 'No description')}",
        f"> Total sessions: {project.get('total_sessions', 0)}",
        "",
    ]


def _format_last_activity_section(breadcrumbs: dict) -> list:
    last = breadcrumbs.get("last_activity", {})
    if not last.get("summary"):
        return []
    return [
        "## Last Activity",
        f"**Summary:** {last['summary']}",
        f"**Next focus:** {last.get('next_focus', 'Continue project work')}",
        "",
    ]


def _format_findings_section(breadcrumbs: dict) -> list:
    findings = breadcrumbs.get("findings", [])
    if not findings:
        return []
    lines = ["## Key Findings"]
    for f in findings:
        lines.append(f"- {f}")
    lines.append("")
    return lines


def _format_unknowns_section(breadcrumbs: dict) -> list:
    unknowns = breadcrumbs.get("unknowns", [])
    unresolved = [u for u in unknowns if not u.get("is_resolved", False)]
    if not unresolved:
        return []
    lines = ["## Remaining Unknowns"]
    for u in unresolved:
        lines.append(f"- {u['unknown']}")
    lines.append("")
    return lines


def _format_dead_ends_section(breadcrumbs: dict) -> list:
    dead_ends = breadcrumbs.get("dead_ends", [])
    if not dead_ends:
        return []
    lines = ["## Dead Ends (Avoid These)"]
    for d in dead_ends:
        lines.append(f"- **{d['approach']}** - {d['why_failed']}")
    lines.append("")
    return lines


def _format_mistakes_section(breadcrumbs: dict) -> list:
    mistakes = breadcrumbs.get("mistakes_to_avoid", [])
    if not mistakes:
        return []
    lines = ["## Mistakes to Avoid"]
    for m in mistakes:
        lines.append(f"- **{m['mistake']}** → {m['prevention']} (cost: {m.get('cost', 'unknown')})")
    lines.append("")
    return lines


def _format_incomplete_work_section(breadcrumbs: dict) -> list:
    incomplete = breadcrumbs.get("incomplete_work", [])
    if not incomplete:
        return []
    lines = ["## Incomplete Work"]
    for item in incomplete:
        goal = item.get("goal") or item.get("objective", "Unknown goal")
        progress = item.get("progress", "unknown")
        lines.append(f"- {goal} ({progress})")
    lines.append("")
    return lines


def _format_skill_entry(skill: dict) -> list:
    lines = [
        f"### {skill.get('title') or skill.get('id', 'Unknown Skill')}",
        f"**Tags:** {', '.join(skill.get('tags', []))}",
    ]
    if skill.get("summary"):
        lines.append(f"**Summary:** {skill['summary']}")
    if skill.get("steps"):
        lines.append("**Steps:**")
        for step in skill["steps"]:
            lines.append(f"1. {step}")
    if skill.get("gotchas"):
        lines.append("**Gotchas:**")
        for gotcha in skill["gotchas"]:
            lines.append(f"- ⚠️ {gotcha}")
    if skill.get("references"):
        lines.append("**References:**")
        for ref in skill["references"]:
            lines.append(f"- {ref}")
    lines.append("")
    return lines


def _format_skills_section(breadcrumbs: dict) -> list:
    full_skills = breadcrumbs.get("full_skills", [])
    non_empty_skills = [s for s in full_skills if s.get("summary") or s.get("steps") or s.get("gotchas")]
    if not non_empty_skills:
        return []
    lines = ["## Relevant Skills"]
    for skill in non_empty_skills:
        lines.extend(_format_skill_entry(skill))
    return lines


def _format_context_budget_section(breadcrumbs: dict) -> list:
    budget = breadcrumbs.get("context_budget")
    if not budget:
        return []
    return [
        "## Context Budget",
        f"**Task complexity:** {budget.get('task_complexity', 'medium')}",
        f"**Total tokens:** {budget.get('total_tokens', 0)}",
        "",
    ]


def _format_reference_docs_section(breadcrumbs: dict) -> list:
    ref_docs = breadcrumbs.get("reference_docs", [])
    if not ref_docs:
        return []
    lines = ["## Reference Documentation"]
    for doc in ref_docs:
        lines.append(f"- `{doc['path']}` ({doc['type']}) - {doc['description']}")
    lines.append("")
    return lines


def _format_recent_artifacts_section(breadcrumbs: dict) -> list:
    artifacts = breadcrumbs.get("recent_artifacts", [])
    if not artifacts:
        return []
    lines = ["## Recent File Changes"]
    for art in artifacts[:5]:  # Top 5
        lines.append(f"- {art.get('ai_id', 'unknown')}: {art.get('task_summary', '')}")
        if art.get("files_modified"):
            for file in art["files_modified"][:3]:  # Top 3 files
                lines.append(f"  - `{file}`")
    lines.append("")
    return lines


def generate_context_markdown(breadcrumbs: dict) -> str:
    """
    Generate markdown-formatted context for injection into AI prompts.

    Args:
        breadcrumbs: Dictionary from bootstrap_project_breadcrumbs()

    Returns:
        Markdown string formatted for context injection
    """
    section_builders = [
        _format_project_header,
        _format_last_activity_section,
        _format_findings_section,
        _format_unknowns_section,
        _format_dead_ends_section,
        _format_mistakes_section,
        _format_incomplete_work_section,
        _format_skills_section,
        _format_context_budget_section,
        _format_reference_docs_section,
        _format_recent_artifacts_section,
    ]
    lines = []
    for builder in section_builders:
        lines.extend(builder(breadcrumbs))
    return "\n".join(lines)
