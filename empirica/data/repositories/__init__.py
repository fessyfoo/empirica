"""
Repository pattern implementation for session database domains.

Each repository encapsulates database operations for a specific domain,
sharing a single SQLite connection for transactional consistency.
"""

from .base import BaseRepository
from .branches import BranchRepository
from .breadcrumbs import BreadcrumbRepository
from .cascades import CascadeRepository
from .codebase_model import CodebaseModelRepository
from .goals import GoalDataRepository
from .metrics import MetricsRepository
from .projects import ProjectRepository
from .sessions import SessionRepository
from .utilities import CommandRepository, TokenRepository, WorkspaceRepository
from .vectors import VectorRepository

__all__ = [
    "BaseRepository",
    "BranchRepository",
    "BreadcrumbRepository",
    "CascadeRepository",
    "CodebaseModelRepository",
    "CommandRepository",
    "GoalDataRepository",
    "MetricsRepository",
    "ProjectRepository",
    "SessionRepository",
    "TokenRepository",
    "VectorRepository",
    "WorkspaceRepository",
]
