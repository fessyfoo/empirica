"""
Epistemic Handoff Reports - Phase 1.6

Compressed, semantically-rich session summaries for multi-agent coordination.

Combines:
- Git checkpoint efficiency (~85% token reduction)
- Semantic context (what was learned, what's next)
- Queryable history (multi-agent coordination)

Target: ~1,250 tokens for complete session resumption (vs 20,000 baseline)
"""

from .report_generator import EpistemicHandoffReportGenerator
from .storage import DatabaseHandoffStorage, GitHandoffStorage

__all__ = [
    "DatabaseHandoffStorage",
    "EpistemicHandoffReportGenerator",
    "GitHandoffStorage",
]
