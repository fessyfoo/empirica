"""
Codebase Model Type Definitions

Dataclasses for temporal entity tracking in codebases.
Follows Empirica conventions: @dataclass, to_dict/from_dict, time.time() timestamps.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(Enum):
    """Types of codebase entities."""

    FUNCTION = "function"
    CLASS = "class"
    API = "api"
    FILE = "file"
    PACKAGE = "package"
    CONSTANT = "constant"
    MODULE = "module"


class RelationshipType(Enum):
    """Directional relationship between entities."""

    CALLS = "calls"
    IMPORTS = "imports"
    DEPENDS_ON = "depends_on"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    USES = "uses"
    FIXES = "fixes"


class FactStatus(Enum):
    """Lifecycle status of a fact."""

    CANONICAL = "canonical"  # Primary assertion from source code
    CORROBORATED = "corroborated"  # Confirmed by multiple observations
    SUPERSEDED = "superseded"  # Replaced by newer fact
    INFERRED = "inferred"  # Derived from pattern matching


class EvidenceType(Enum):
    """How the fact was established."""

    SOURCE_CODE = "source_code"
    USER_CORRECTION = "user_correction"
    BUG_FIX = "bug_fix"
    TEST = "test"
    SESSION = "session"


class ConstraintType(Enum):
    """Type of learned constraint."""

    LINTING = "linting"
    ARCHITECTURE = "architecture"
    TESTING = "testing"
    API_CONTRACT = "api_contract"
    STYLE = "style"
    CONVENTION = "convention"


@dataclass
class Entity:
    """
    A resolved identity in the codebase.

    Entities persist across sessions. first_seen/last_seen track temporal validity.
    last_seen=None means still active (not invalidated).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity_type: str = "function"  # EntityType.value
    name: str = ""
    file_path: str | None = None
    signature: str | None = None  # e.g. "def foo(bar: str) -> int"
    first_seen: float = field(default_factory=time.time)
    last_seen: float | None = None  # None = still exists
    project_id: str | None = None
    session_id: str | None = None  # Session that discovered it
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.entity_type not in {e.value for e in EntityType}:
            raise ValueError(f"Invalid entity_type: {self.entity_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "name": self.name,
            "file_path": self.file_path,
            "signature": self.signature,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Entity":
        return Entity(
            id=data.get("id", str(uuid.uuid4())),
            entity_type=data.get("entity_type", "function"),
            name=data.get("name", ""),
            file_path=data.get("file_path"),
            signature=data.get("signature"),
            first_seen=float(data.get("first_seen", time.time())),
            last_seen=float(data["last_seen"]) if data.get("last_seen") else None,
            project_id=data.get("project_id"),
            session_id=data.get("session_id"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Fact:
    """
    A temporal assertion about the codebase.

    Facts have validity windows (valid_at → invalid_at) and evidence chains.
    invalid_at=None means the fact is still true.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fact_text: str = ""
    valid_at: float = field(default_factory=time.time)
    invalid_at: float | None = None  # None = still true
    status: str = "canonical"  # FactStatus.value
    entity_ids: list[str] = field(default_factory=list)
    evidence_type: str = "source_code"  # EvidenceType.value
    evidence_path: str = ""  # file:lines or session_id
    confidence: float = 1.0
    project_id: str | None = None
    session_id: str | None = None

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fact_text": self.fact_text,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "status": self.status,
            "entity_ids": self.entity_ids,
            "evidence_type": self.evidence_type,
            "evidence_path": self.evidence_path,
            "confidence": self.confidence,
            "project_id": self.project_id,
            "session_id": self.session_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Fact":
        return Fact(
            id=data.get("id", str(uuid.uuid4())),
            fact_text=data.get("fact_text", ""),
            valid_at=float(data.get("valid_at", time.time())),
            invalid_at=float(data["invalid_at"]) if data.get("invalid_at") else None,
            status=data.get("status", "canonical"),
            entity_ids=data.get("entity_ids", []),
            evidence_type=data.get("evidence_type", "source_code"),
            evidence_path=data.get("evidence_path", ""),
            confidence=float(data.get("confidence", 1.0)),
            project_id=data.get("project_id"),
            session_id=data.get("session_id"),
        )


@dataclass
class Relationship:
    """Directional link between two entities."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_entity_id: str = ""
    target_entity_id: str = ""
    relationship_type: str = "calls"  # RelationshipType.value
    weight: float = 1.0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    evidence_count: int = 1
    project_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relationship_type": self.relationship_type,
            "weight": self.weight,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "evidence_count": self.evidence_count,
            "project_id": self.project_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Relationship":
        return Relationship(
            id=data.get("id", str(uuid.uuid4())),
            source_entity_id=data.get("source_entity_id", ""),
            target_entity_id=data.get("target_entity_id", ""),
            relationship_type=data.get("relationship_type", "calls"),
            weight=float(data.get("weight", 1.0)),
            first_seen=float(data.get("first_seen", time.time())),
            last_seen=float(data.get("last_seen", time.time())),
            evidence_count=int(data.get("evidence_count", 1)),
            project_id=data.get("project_id"),
        )


@dataclass
class Constraint:
    """
    A learned pattern from user corrections or conventions.

    Extends Empirica's lesson concept with file-scoped targeting.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    constraint_type: str = "convention"  # ConstraintType.value
    rule_name: str = ""
    file_pattern: str | None = None  # Glob: "src/**/*.py"
    description: str = ""
    violation_count: int = 0
    last_violated: float | None = None
    examples: list[dict[str, str]] = field(default_factory=list)  # [{incorrect, correct}]
    severity: str = "warning"  # error, warning, info
    project_id: str | None = None
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "constraint_type": self.constraint_type,
            "rule_name": self.rule_name,
            "file_pattern": self.file_pattern,
            "description": self.description,
            "violation_count": self.violation_count,
            "last_violated": self.last_violated,
            "examples": self.examples,
            "severity": self.severity,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Constraint":
        return Constraint(
            id=data.get("id", str(uuid.uuid4())),
            constraint_type=data.get("constraint_type", "convention"),
            rule_name=data.get("rule_name", ""),
            file_pattern=data.get("file_pattern"),
            description=data.get("description", ""),
            violation_count=int(data.get("violation_count", 0)),
            last_violated=float(data["last_violated"]) if data.get("last_violated") else None,
            examples=data.get("examples", []),
            severity=data.get("severity", "warning"),
            project_id=data.get("project_id"),
            session_id=data.get("session_id"),
            created_at=float(data.get("created_at", time.time())),
        )
