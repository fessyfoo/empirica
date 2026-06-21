"""
Test content-hash deduplication for breadcrumb logging.

Verifies that log_finding, log_unknown, and log_dead_end correctly:
1. Return the same ID when logging identical content twice
2. Create new entries for genuinely different content
3. Normalize whitespace/casing before comparison (dedup despite cosmetic differences)
"""

import uuid

import pytest


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh SessionDatabase with temp SQLite file."""
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / "test_dedup.db"
    db = SessionDatabase(db_path=str(db_path))
    yield db
    db.close()


# Fixed IDs for all tests
PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


# ── Finding dedup ────────────────────────────────────────────────────────────


class TestFindingDedup:
    def test_exact_duplicate_returns_same_id(self, fresh_db):
        """Logging the exact same finding text twice should return the same ID."""
        repo = fresh_db.breadcrumbs
        text = "The API rate limit is 100 requests per minute"

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, text)
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, text)

        assert id1 == id2, f"Expected same ID for duplicate finding, got {id1} vs {id2}"

    def test_different_text_creates_new_entry(self, fresh_db):
        """Genuinely different finding text should produce different IDs."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, "The API uses REST")
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, "The API uses GraphQL")

        assert id1 != id2, "Different findings should have different IDs"

    def test_whitespace_normalization(self, fresh_db):
        """Same content with different whitespace should dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, "The  server   uses  nginx")
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, "The server uses nginx")

        assert id1 == id2, "Whitespace-normalized findings should dedup"

    def test_case_normalization(self, fresh_db):
        """Same content with different casing should dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, "Database uses PostgreSQL")
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, "database uses postgresql")

        assert id1 == id2, "Case-normalized findings should dedup"

    def test_leading_trailing_whitespace(self, fresh_db):
        """Leading/trailing whitespace should be stripped for dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, "  important finding  ")
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, "important finding")

        assert id1 == id2, "Stripped whitespace findings should dedup"

    def test_different_projects_not_deduped(self, fresh_db):
        """Same text in different projects should NOT dedup."""
        repo = fresh_db.breadcrumbs
        text = "Shared finding text across projects"
        project2 = str(uuid.uuid4())

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, text)
        id2 = repo.log_finding(project2, SESSION_ID, text)

        assert id1 != id2, "Same finding in different projects should not dedup"


# ── Unknown dedup ────────────────────────────────────────────────────────────


class TestUnknownDedup:
    def test_exact_duplicate_returns_same_id(self, fresh_db):
        """Logging the exact same unknown text twice should return the same ID."""
        repo = fresh_db.breadcrumbs
        text = "How does the auth token refresh work?"

        id1 = repo.log_unknown(PROJECT_ID, SESSION_ID, text)
        id2 = repo.log_unknown(PROJECT_ID, SESSION_ID, text)

        assert id1 == id2, f"Expected same ID for duplicate unknown, got {id1} vs {id2}"

    def test_different_text_creates_new_entry(self, fresh_db):
        """Genuinely different unknown text should produce different IDs."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_unknown(PROJECT_ID, SESSION_ID, "What is the DB schema?")
        id2 = repo.log_unknown(PROJECT_ID, SESSION_ID, "What is the API contract?")

        assert id1 != id2, "Different unknowns should have different IDs"

    def test_whitespace_and_case_normalization(self, fresh_db):
        """Same content with different whitespace and casing should dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_unknown(PROJECT_ID, SESSION_ID, "  HOW does   caching  WORK?  ")
        id2 = repo.log_unknown(PROJECT_ID, SESSION_ID, "how does caching work?")

        assert id1 == id2, "Normalized unknowns should dedup"


# ── Dead-end dedup ───────────────────────────────────────────────────────────


class TestDeadEndDedup:
    def test_exact_duplicate_returns_same_id(self, fresh_db):
        """Logging the same approach+why_failed twice should return the same ID."""
        repo = fresh_db.breadcrumbs
        approach = "Tried monkey-patching the module"
        why_failed = "It broke the import chain"

        id1 = repo.log_dead_end(PROJECT_ID, SESSION_ID, approach, why_failed)
        id2 = repo.log_dead_end(PROJECT_ID, SESSION_ID, approach, why_failed)

        assert id1 == id2, f"Expected same ID for duplicate dead end, got {id1} vs {id2}"

    def test_different_approach_creates_new_entry(self, fresh_db):
        """Different approach text should produce a new dead-end entry."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "Used regex parsing", "Too fragile")
        id2 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "Used AST parsing", "Too fragile")

        assert id1 != id2, "Different approaches should not dedup"

    def test_different_why_failed_creates_new_entry(self, fresh_db):
        """Different why_failed text should produce a new dead-end entry."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "Same approach", "Reason A")
        id2 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "Same approach", "Reason B")

        assert id1 != id2, "Different failure reasons should not dedup"

    def test_whitespace_and_case_normalization(self, fresh_db):
        """Same content with different whitespace/casing should dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "  Tried  DIRECT  SQL  ", "  PERMISSIONS  denied  ")
        id2 = repo.log_dead_end(PROJECT_ID, SESSION_ID, "tried direct sql", "permissions denied")

        assert id1 == id2, "Normalized dead ends should dedup"


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestDedupEdgeCases:
    def test_finding_only_one_row_stored(self, fresh_db):
        """After dedup, only one row should exist in the table."""
        repo = fresh_db.breadcrumbs
        text = "Only one row should exist"

        repo.log_finding(PROJECT_ID, SESSION_ID, text)
        repo.log_finding(PROJECT_ID, SESSION_ID, text)
        repo.log_finding(PROJECT_ID, SESSION_ID, text)

        findings = repo.get_project_findings(PROJECT_ID, depth="complete")
        matching = [f for f in findings if f["finding"] == text]
        assert len(matching) == 1, f"Expected 1 row, got {len(matching)}"

    def test_unknown_only_one_row_stored(self, fresh_db):
        """After dedup, only one row should exist in the table."""
        repo = fresh_db.breadcrumbs
        text = "Only one unknown row"

        repo.log_unknown(PROJECT_ID, SESSION_ID, text)
        repo.log_unknown(PROJECT_ID, SESSION_ID, text)

        unknowns = repo.get_project_unknowns(PROJECT_ID)
        matching = [u for u in unknowns if u["unknown"] == text]
        assert len(matching) == 1, f"Expected 1 row, got {len(matching)}"

    def test_dead_end_only_one_row_stored(self, fresh_db):
        """After dedup, only one row should exist in the table."""
        repo = fresh_db.breadcrumbs
        approach = "Same approach repeated"
        why = "Same reason repeated"

        repo.log_dead_end(PROJECT_ID, SESSION_ID, approach, why)
        repo.log_dead_end(PROJECT_ID, SESSION_ID, approach, why)

        dead_ends = repo.get_project_dead_ends(PROJECT_ID)
        matching = [d for d in dead_ends if d["approach"] == approach]
        assert len(matching) == 1, f"Expected 1 row, got {len(matching)}"

    def test_tabs_and_newlines_normalized(self, fresh_db):
        """Tabs and newlines should be collapsed to single spaces for dedup."""
        repo = fresh_db.breadcrumbs

        id1 = repo.log_finding(PROJECT_ID, SESSION_ID, "line one\n\tline two")
        id2 = repo.log_finding(PROJECT_ID, SESSION_ID, "line one line two")

        assert id1 == id2, "Tabs/newlines should normalize to spaces for dedup"
