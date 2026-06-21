from argparse import ArgumentParser
from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.docs_commands import _auto_detect_project_config
from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers
from empirica.core.qdrant.memory import search


def _build_cli_parser() -> ArgumentParser:
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_checkpoint_parsers(subparsers)
    return parser


def test_project_search_focused_includes_docs_by_default():
    client = type("DummyClient", (), {"collection_exists": lambda self, name: False})()

    with (
        patch("empirica.core.qdrant.memory._check_qdrant_available", return_value=True),
        patch("empirica.core.qdrant.memory._get_qdrant_client", return_value=client),
    ):
        results = search("project-id", "workflow state model")

    assert set(results.keys()) == {"docs", "eidetic", "episodic"}
    assert results["docs"] == []


def test_project_search_parser_help_matches_focused_default():
    parser = _build_cli_parser()

    args = parser.parse_args(
        [
            "project-search",
            "--project-id",
            "proj-123",
            "--task",
            "workflow state model",
        ]
    )

    assert args.type == "focused"


def test_auto_detect_project_config_handles_missing_pyproject(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (tmp_path / ".docsignore").write_text("Generated*\nscripts/\n", encoding="utf-8")

    config = _auto_detect_project_config(tmp_path)

    assert config.project_name == tmp_path.name
    assert config.docs_ignore_classes == ["Generated*"]
    assert config.docs_ignore_paths == ["scripts/"]
