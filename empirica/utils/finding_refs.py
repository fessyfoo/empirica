"""
Utilities for parsing and managing structured references in findings.

Foundation for future Qdrant/Sentinel integration.
"""

import re
import subprocess
from typing import Any


def get_current_git_commit(repo_path: str = ".") -> str | None:
    """Get current git commit SHA"""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def parse_file_references(text: str) -> list[dict[str, Any]]:
    """
    Extract structured file references from finding text.

    Patterns recognized:
    - file.py:45 -> {"file": "file.py", "line": 45}
    - file.py:45-52 -> {"file": "file.py", "lines": [45, 52]}
    - path/to/file.py:100 -> {"file": "path/to/file.py", "line": 100}

    Args:
        text: Finding text possibly containing file references

    Returns:
        List of structured references
    """
    refs = []

    # Pattern: filename.ext:line or filename.ext:line1-line2
    # Handles: auth.py:45, src/auth.py:45, auth.py:45-52
    pattern = r"([\w/.-]+\.[\w]+):(\d+)(?:-(\d+))?"

    for match in re.finditer(pattern, text):
        file_path = match.group(1)
        line_start = int(match.group(2))
        line_end = match.group(3)

        ref = {"file": file_path}

        if line_end:
            ref["lines"] = [line_start, int(line_end)]
        else:
            ref["line"] = line_start

        refs.append(ref)

    return refs


def parse_doc_references(text: str) -> list[dict[str, str]]:
    """
    Extract documentation references.

    Patterns:
    - docs/guide.md -> {"doc": "docs/guide.md"}
    - README.md#section -> {"doc": "README.md", "section": "#section"}
    """
    refs = []

    # Pattern: doc files with optional anchor
    pattern = r"([\w/.-]+\.md)(?:(#[\w-]+))?"

    for match in re.finditer(pattern, text):
        doc_path = match.group(1)
        section = match.group(2)

        ref = {"doc": doc_path}
        if section:
            ref["section"] = section

        refs.append(ref)

    return refs


def parse_url_references(text: str) -> list[str]:
    """Extract URLs from text"""
    # Simple URL pattern
    pattern = r"https?://[^\s)]+|www\.[^\s)]+"
    return list(set(re.findall(pattern, text)))


def structure_finding(
    finding_text: str, commit_sha: str | None = None, session_id: str | None = None, check_id: str | None = None
) -> dict[str, Any]:
    """
    Convert plain text finding into structured format.

    Args:
        finding_text: The finding text
        commit_sha: Git commit SHA (auto-detected if None)
        session_id: Session ID
        check_id: CHECK assessment ID

    Returns:
        Structured finding with refs
    """
    if commit_sha is None:
        commit_sha = get_current_git_commit()

    structured = {
        "text": finding_text,
        "refs": {
            "files": parse_file_references(finding_text),
            "docs": parse_doc_references(finding_text),
            "urls": parse_url_references(finding_text),
        },
        "commit": commit_sha,
    }

    if session_id:
        structured["session_id"] = session_id
    if check_id:
        structured["check_id"] = check_id

    return structured


def structure_findings_list(
    findings: list[str], commit_sha: str | None = None, session_id: str | None = None, check_id: str | None = None
) -> list[dict[str, Any]]:
    """Structure a list of findings"""
    if commit_sha is None:
        commit_sha = get_current_git_commit()

    return [structure_finding(f, commit_sha, session_id, check_id) for f in findings]


# Query helpers for future use


def filter_findings_by_file(findings: list[dict[str, Any]], filename: str) -> list[dict[str, Any]]:
    """Filter structured findings by filename"""
    return [f for f in findings if any(filename in ref.get("file", "") for ref in f.get("refs", {}).get("files", []))]


def filter_findings_by_commit(findings: list[dict[str, Any]], commit_sha: str) -> list[dict[str, Any]]:
    """Filter findings by git commit"""
    return [f for f in findings if f.get("commit") == commit_sha]


def get_file_refs_from_findings(findings: list[dict[str, Any]]) -> list[str]:
    """Extract all unique file references from findings"""
    files = set()
    for finding in findings:
        for ref in finding.get("refs", {}).get("files", []):
            files.add(ref.get("file", ""))
    return sorted(files)
