"""
Test git state capture in checkpoints (Phase 2.5)
"""

import subprocess

import pytest

from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger


class TestGitStateCapture:
    """Test git state capture functionality"""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a git repository for testing"""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_path, check=True)

        # Create initial commit
        (repo_path / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

        return repo_path

    @pytest.fixture
    def logger(self, git_repo):
        """Create logger with git state capture enabled"""
        return GitEnhancedReflexLogger(
            session_id="test-git-state-session",
            enable_git_notes=True,
            base_log_dir=str(git_repo / ".empirica_reflex_logs"),
            git_repo_path=str(git_repo),
        )

    def test_checkpoint_includes_git_state(self, logger, git_repo):
        """Test that checkpoints include git_state field"""
        # Create checkpoint
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6, "do": 0.7, "context": 0.8})

        # Retrieve checkpoint
        checkpoint = logger.get_last_checkpoint()

        # Verify git_state exists
        assert "git_state" in checkpoint
        assert "head_commit" in checkpoint["git_state"]
        assert "commits_since_last_checkpoint" in checkpoint["git_state"]
        assert "uncommitted_changes" in checkpoint["git_state"]

    def test_git_state_captures_head_commit(self, logger, git_repo):
        """Test that git_state captures current HEAD commit"""
        # Get current HEAD
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=git_repo)
        expected_head = result.stdout.strip()

        # Create checkpoint
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})
        checkpoint = logger.get_last_checkpoint()

        # Verify HEAD captured
        assert checkpoint["git_state"]["head_commit"] == expected_head

    def test_git_state_tracks_commits_between_checkpoints(self, logger, git_repo):
        """Test that commits between checkpoints are tracked"""
        # First checkpoint
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})

        # Make a commit
        (git_repo / "feature.py").write_text("# New feature\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add feature"], cwd=git_repo, check=True)

        # Second checkpoint
        logger.add_checkpoint("CHECK", 1, {"know": 0.7})
        checkpoint = logger.get_last_checkpoint()

        # Verify commit tracked
        commits = checkpoint["git_state"]["commits_since_last_checkpoint"]
        assert len(commits) >= 1
        assert any("feat: add feature" in c["message"] for c in commits)
        assert any("feature.py" in c.get("files_changed", []) for c in commits)

    def test_git_state_captures_uncommitted_changes(self, logger, git_repo):
        """Test that uncommitted changes are captured"""
        # Create checkpoint
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})

        # Make uncommitted changes
        (git_repo / "uncommitted.py").write_text("# Uncommitted work\n")
        subprocess.run(["git", "add", "uncommitted.py"], cwd=git_repo, check=True)

        # Second checkpoint
        logger.add_checkpoint("ACT", 1, {"know": 0.8})
        checkpoint = logger.get_last_checkpoint()

        # Verify uncommitted changes tracked
        uncommitted = checkpoint["git_state"]["uncommitted_changes"]
        assert "uncommitted.py" in uncommitted.get("files_added", [])

    def test_learning_delta_calculation(self, logger):
        """Test that learning deltas are calculated"""
        # First checkpoint
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6, "do": 0.7})

        # Second checkpoint with increased vectors
        logger.add_checkpoint("CHECK", 1, {"know": 0.8, "do": 0.9})
        checkpoint = logger.get_last_checkpoint()

        # Verify learning delta
        assert "learning_delta" in checkpoint

        know_delta = checkpoint["learning_delta"].get("know", {})
        assert know_delta["prev"] == 0.6
        assert know_delta["curr"] == 0.8
        assert know_delta["delta"] == pytest.approx(0.2, abs=0.01)

        do_delta = checkpoint["learning_delta"].get("do", {})
        assert do_delta["prev"] == 0.7
        assert do_delta["curr"] == 0.9
        assert do_delta["delta"] == pytest.approx(0.2, abs=0.01)

    def test_git_state_handles_no_previous_checkpoint(self, logger):
        """Test that first checkpoint handles missing previous state gracefully"""
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})
        checkpoint = logger.get_last_checkpoint()

        # Git state should exist but commits_since_last should be empty
        assert "git_state" in checkpoint
        assert checkpoint["git_state"]["commits_since_last_checkpoint"] == []

        # Learning delta should be empty (no previous checkpoint)
        assert checkpoint.get("learning_delta", {}) == {}

    def test_git_state_with_multiple_commits(self, logger, git_repo):
        """Test tracking multiple commits between checkpoints"""
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})

        # Make multiple commits
        for i in range(3):
            (git_repo / f"file{i}.py").write_text(f"# File {i}\n")
            subprocess.run(["git", "add", f"file{i}.py"], cwd=git_repo, check=True)
            subprocess.run(["git", "commit", "-m", f"feat: add file{i}"], cwd=git_repo, check=True)

        logger.add_checkpoint("ACT", 1, {"know": 0.9})
        checkpoint = logger.get_last_checkpoint()

        # Verify all commits tracked
        commits = checkpoint["git_state"]["commits_since_last_checkpoint"]
        assert len(commits) >= 3

        messages = [c["message"] for c in commits]
        assert any("file0" in m for m in messages)
        assert any("file1" in m for m in messages)
        assert any("file2" in m for m in messages)

    def test_checkpoint_token_count_includes_git_state(self, logger, git_repo):
        """Test that token count reflects git_state addition"""
        # Checkpoint without git commits
        logger.add_checkpoint("PREFLIGHT", 1, {"know": 0.6})
        checkpoint1 = logger.get_last_checkpoint()
        token_count_1 = checkpoint1["token_count"]

        # Make commits
        (git_repo / "big_feature.py").write_text("# Feature\n" * 10)
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: big feature"], cwd=git_repo, check=True)

        # Checkpoint with git commits
        logger.add_checkpoint("ACT", 1, {"know": 0.9})
        checkpoint2 = logger.get_last_checkpoint()
        token_count_2 = checkpoint2["token_count"]

        # Token count should be higher with git state
        assert token_count_2 > token_count_1
