"""
Entity Extractor — Regex-based codebase entity extraction from file edits.

Extracts functions, classes, APIs, and imports from diffs/content using
language-specific regex patterns. No external dependencies — stdlib only.

Adapted from world-model-mcp's extraction.py (MIT license).
"""

import os
import re

from .types import Entity, Fact, Relationship


def detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".sh": "shell",
        ".bash": "shell",
    }.get(ext, "unknown")


def extract_entities_from_content(
    file_path: str,
    content: str,
    project_id: str | None = None,
    session_id: str | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """
    Extract entities and relationships from file content.

    Returns (entities, relationships) where relationships are import/call edges.
    """
    lang = detect_language(file_path)

    if lang == "python":
        return _extract_python(file_path, content, project_id, session_id)
    elif lang in ("typescript", "javascript"):
        return _extract_typescript(file_path, content, project_id, session_id)
    elif lang == "go":
        return _extract_go(file_path, content, project_id, session_id)
    elif lang == "rust":
        return _extract_rust(file_path, content, project_id, session_id)
    else:
        return [], []


def extract_entities_from_diff(
    file_path: str,
    diff_text: str,
    project_id: str | None = None,
    session_id: str | None = None,
) -> tuple[list[Entity], list[Fact]]:
    """
    Extract entities and facts from a diff (added lines only).

    Lighter than full content extraction — only looks at new/changed lines.
    Returns (entities, facts) where facts are assertions about the change.
    """
    # Extract only added lines from diff
    added_lines = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    added_content = "\n".join(added_lines)
    if not added_content.strip():
        return [], []

    entities, _ = extract_entities_from_content(file_path, added_content, project_id, session_id)

    # Generate facts from the diff
    facts = _extract_facts_from_diff(file_path, diff_text, entities)

    return entities, facts


# ============================================================================
# Python Extraction
# ============================================================================

_PY_FUNC = re.compile(
    r"^(?:async\s+)?def\s+(\w+)\s*\((.*?)\)(?:\s*->\s*(.+?))?:",
    re.MULTILINE,
)
_PY_CLASS = re.compile(
    r"^class\s+(\w+)(?:\((.*?)\))?:",
    re.MULTILINE,
)
_PY_IMPORT_FROM = re.compile(
    r"^from\s+([\w.]+)\s+import\s+(.+)",
    re.MULTILINE,
)
_PY_DECORATOR_ROUTE = re.compile(
    r'@(?:app|router|blueprint)\.(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]',
    re.MULTILINE,
)
_PY_CONSTANT = re.compile(
    r"^([A-Z][A-Z_0-9]+)\s*[=:]",
    re.MULTILINE,
)


def _extract_python(
    file_path: str,
    content: str,
    project_id: str | None,
    session_id: str | None,
) -> tuple[list[Entity], list[Relationship]]:
    entities: list[Entity] = []
    relationships: list[Relationship] = []

    # Functions
    for m in _PY_FUNC.finditer(content):
        name = m.group(1)
        params = m.group(2)
        ret = m.group(3)
        sig = f"def {name}({params})"
        if ret:
            sig += f" -> {ret}"
        entities.append(
            Entity(
                entity_type="function",
                name=name,
                file_path=file_path,
                signature=sig,
                project_id=project_id,
                session_id=session_id,
            )
        )

    # Classes
    for m in _PY_CLASS.finditer(content):
        name = m.group(1)
        bases = m.group(2) or ""
        entities.append(
            Entity(
                entity_type="class",
                name=name,
                file_path=file_path,
                signature=f"class {name}({bases})" if bases else f"class {name}",
                project_id=project_id,
                session_id=session_id,
            )
        )
        # Inheritance relationships
        if bases:
            for base in re.split(r"\s*,\s*", bases):
                base = base.strip()
                if base and base not in ("object",):
                    relationships.append(
                        Relationship(
                            source_entity_id=name,  # Resolved later by caller
                            target_entity_id=base,
                            relationship_type="extends",
                            project_id=project_id,
                        )
                    )

    # API routes (Flask/FastAPI)
    for m in _PY_DECORATOR_ROUTE.finditer(content):
        method = m.group(1).upper()
        path = m.group(2)
        entities.append(
            Entity(
                entity_type="api",
                name=f"{method} {path}",
                file_path=file_path,
                signature=f"@app.{m.group(1)}('{path}')",
                project_id=project_id,
                session_id=session_id,
            )
        )

    # Constants (ALL_CAPS)
    for m in _PY_CONSTANT.finditer(content):
        name = m.group(1)
        entities.append(
            Entity(
                entity_type="constant",
                name=name,
                file_path=file_path,
                project_id=project_id,
                session_id=session_id,
            )
        )

    # Import relationships (from X import Y)
    for m in _PY_IMPORT_FROM.finditer(content):
        module = m.group(1)
        relationships.append(
            Relationship(
                source_entity_id=file_path,  # Resolved later by caller
                target_entity_id=module,
                relationship_type="imports",
                project_id=project_id,
            )
        )

    return entities, relationships


# ============================================================================
# TypeScript / JavaScript Extraction
# ============================================================================

_TS_FUNC = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\((.*?)\)",
    re.MULTILINE,
)
_TS_ARROW = re.compile(
    r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\((.*?)\)\s*(?::\s*\S+\s*)?=>",
    re.MULTILINE,
)
_TS_CLASS = re.compile(
    r"(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?",
    re.MULTILINE,
)
_TS_INTERFACE = re.compile(
    r"(?:export\s+)?interface\s+(\w+)",
    re.MULTILINE,
)
_TS_API = re.compile(
    r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]',
    re.MULTILINE,
)


def _extract_typescript(
    file_path: str,
    content: str,
    project_id: str | None,
    session_id: str | None,
) -> tuple[list[Entity], list[Relationship]]:
    entities: list[Entity] = []
    relationships: list[Relationship] = []

    for m in _TS_FUNC.finditer(content):
        entities.append(
            Entity(
                entity_type="function",
                name=m.group(1),
                file_path=file_path,
                signature=f"function {m.group(1)}({m.group(2)})",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _TS_ARROW.finditer(content):
        entities.append(
            Entity(
                entity_type="function",
                name=m.group(1),
                file_path=file_path,
                signature=f"const {m.group(1)} = ({m.group(2)}) =>",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _TS_CLASS.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"class {m.group(1)}",
                project_id=project_id,
                session_id=session_id,
            )
        )
        if m.group(2):
            relationships.append(
                Relationship(
                    source_entity_id=m.group(1),
                    target_entity_id=m.group(2),
                    relationship_type="extends",
                    project_id=project_id,
                )
            )

    for m in _TS_INTERFACE.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"interface {m.group(1)}",
                project_id=project_id,
                session_id=session_id,
                metadata={"kind": "interface"},
            )
        )

    for m in _TS_API.finditer(content):
        method = m.group(1).upper()
        path = m.group(2)
        entities.append(
            Entity(
                entity_type="api",
                name=f"{method} {path}",
                file_path=file_path,
                signature=f"app.{m.group(1)}('{path}')",
                project_id=project_id,
                session_id=session_id,
            )
        )

    return entities, relationships


# ============================================================================
# Go Extraction
# ============================================================================

_GO_FUNC = re.compile(
    r"^func\s+(?:\(\w+\s+\*?(\w+)\)\s+)?(\w+)\s*\((.*?)\)",
    re.MULTILINE,
)
_GO_STRUCT = re.compile(
    r"^type\s+(\w+)\s+struct\b",
    re.MULTILINE,
)
_GO_INTERFACE = re.compile(
    r"^type\s+(\w+)\s+interface\b",
    re.MULTILINE,
)


def _extract_go(
    file_path: str,
    content: str,
    project_id: str | None,
    session_id: str | None,
) -> tuple[list[Entity], list[Relationship]]:
    entities: list[Entity] = []
    relationships: list[Relationship] = []

    for m in _GO_FUNC.finditer(content):
        receiver = m.group(1)
        name = m.group(2)
        params = m.group(3)
        full_name = f"{receiver}.{name}" if receiver else name
        entities.append(
            Entity(
                entity_type="function",
                name=full_name,
                file_path=file_path,
                signature=f"func {full_name}({params})",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _GO_STRUCT.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"type {m.group(1)} struct",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _GO_INTERFACE.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"type {m.group(1)} interface",
                project_id=project_id,
                session_id=session_id,
                metadata={"kind": "interface"},
            )
        )

    return entities, relationships


# ============================================================================
# Rust Extraction
# ============================================================================

_RS_FN = re.compile(
    r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<.*?>)?\s*\((.*?)\)",
    re.MULTILINE,
)
_RS_STRUCT = re.compile(
    r"^(?:pub\s+)?struct\s+(\w+)",
    re.MULTILINE,
)
_RS_TRAIT = re.compile(
    r"^(?:pub\s+)?trait\s+(\w+)",
    re.MULTILINE,
)
_RS_IMPL = re.compile(
    r"^impl(?:<.*?>)?\s+(?:(\w+)\s+for\s+)?(\w+)",
    re.MULTILINE,
)


def _extract_rust(
    file_path: str,
    content: str,
    project_id: str | None,
    session_id: str | None,
) -> tuple[list[Entity], list[Relationship]]:
    entities: list[Entity] = []
    relationships: list[Relationship] = []

    for m in _RS_FN.finditer(content):
        entities.append(
            Entity(
                entity_type="function",
                name=m.group(1),
                file_path=file_path,
                signature=f"fn {m.group(1)}({m.group(2)})",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _RS_STRUCT.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"struct {m.group(1)}",
                project_id=project_id,
                session_id=session_id,
            )
        )

    for m in _RS_TRAIT.finditer(content):
        entities.append(
            Entity(
                entity_type="class",
                name=m.group(1),
                file_path=file_path,
                signature=f"trait {m.group(1)}",
                project_id=project_id,
                session_id=session_id,
                metadata={"kind": "trait"},
            )
        )

    for m in _RS_IMPL.finditer(content):
        trait_name = m.group(1)
        struct_name = m.group(2)
        if trait_name:
            relationships.append(
                Relationship(
                    source_entity_id=struct_name,
                    target_entity_id=trait_name,
                    relationship_type="implements",
                    project_id=project_id,
                )
            )

    return entities, relationships


# ============================================================================
# Fact Extraction from Diffs
# ============================================================================


def _extract_facts_from_diff(
    file_path: str,
    diff_text: str,
    entities: list[Entity],
) -> list[Fact]:
    """Generate facts about changes from a diff."""
    facts: list[Fact] = []

    added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))

    if entities:
        names = [e.name for e in entities[:5]]
        fact_text = f"Modified {file_path}: {'added' if added > removed else 'changed'} {', '.join(names)}"
        facts.append(
            Fact(
                fact_text=fact_text,
                evidence_type="source_code",
                evidence_path=file_path,
                entity_ids=[e.id for e in entities],
                confidence=0.9,
            )
        )

    return facts
