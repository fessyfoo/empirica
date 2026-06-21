"""Shared fixtures for security tests"""

import sqlite3

import pytest


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary SQLite database for testing"""
    db_path = tmp_path / "test_security.db"
    conn = sqlite3.connect(str(db_path))

    # Create test table
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            ai_id TEXT,
            data TEXT
        )
    """)
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def mock_session():
    """Create mock session for testing"""
    return {"id": "test-session-123", "ai_id": "test-ai", "cwd": "/tmp/test", "workspace": "/tmp/test"}
