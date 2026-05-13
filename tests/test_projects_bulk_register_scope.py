"""Tests for `projects-bulk-register --force-metadata-update` scope semantic
(Extension Claude v0.7.8 follow-up).

When `--force-metadata-update` is set, bulk-register should only iterate
projects that are ALREADY on Cortex (intersection by name or repo_url)
rather than attempting to register all 27 discovered entries when the
user only meant 'refresh the 7 I've already synced'.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from empirica.cli.command_handlers.projects_commands import (
    _fetch_cortex_collections,
)

# ─── _fetch_cortex_collections ─────────────────────────────────────────


def test_fetch_cortex_collections_returns_list_on_success():
    """Successful GET /v1/collections returns the projects list."""
    fake_body = b'{"projects": [{"name": "alpha", "repo_url": "https://x/a"}, {"name": "beta", "repo_url": null}]}'

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return fake_body

    with patch("urllib.request.urlopen", return_value=_Resp()):
        result = _fetch_cortex_collections("https://cortex.example.com", "sk-test", 10.0)

    assert len(result) == 2
    assert result[0]["name"] == "alpha"


def test_fetch_cortex_collections_returns_empty_on_http_error():
    """HTTPError → empty list (don't break the bulk-register flow)."""
    import urllib.error

    err = urllib.error.HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
    with patch("urllib.request.urlopen", side_effect=err):
        result = _fetch_cortex_collections("https://cortex.example.com", "sk-test", 10.0)
    assert result == []


def test_fetch_cortex_collections_returns_empty_on_network_error():
    """URLError → empty list."""
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
        result = _fetch_cortex_collections("https://cortex.example.com", "sk-test", 10.0)
    assert result == []


def test_fetch_cortex_collections_returns_empty_on_bad_json():
    """Malformed JSON → empty list."""
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"not json"

    with patch("urllib.request.urlopen", return_value=_Resp()):
        result = _fetch_cortex_collections("https://cortex.example.com", "sk-test", 10.0)
    assert result == []


# ─── --force-metadata-update scope filter ─────────────────────────────


def _make_manifest(*names_and_repos: tuple[str, str | None]) -> dict:
    """Build a discover-shaped manifest from (name, repo_url) tuples."""
    return {
        "projects": [
            {"name": name, "path": f"/tmp/{name}", "repo_url": repo}
            for name, repo in names_and_repos
        ]
    }


def test_force_metadata_update_intersects_by_name(monkeypatch, capsys):
    """Discovered: 5. Registered on Cortex: 2 (by name). Should iterate 2."""
    from empirica.cli.command_handlers import projects_commands

    manifest = _make_manifest(
        ("alpha", "https://github.com/x/alpha"),
        ("beta", "https://github.com/x/beta"),
        ("gamma", None),
        ("delta", None),
        ("epsilon", None),
    )

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    cortex_rows = [{"name": "alpha"}, {"name": "delta"}]

    args = SimpleNamespace(
        manifest_path=None, dry_run=False, force_metadata_update=True,
        cortex_url="https://cortex.example.com", api_key="sk-test",
        timeout=10.0, output="json", includes=None, excludes=None,
    )

    with patch.object(projects_commands, "load_manifest", return_value=manifest), \
         patch.object(projects_commands, "_fetch_cortex_collections", return_value=cortex_rows), \
         patch.object(projects_commands, "_register_one_project", side_effect=fake_register):
        projects_commands.handle_projects_bulk_register_command(args)

    posted_names = {p["name"] for p in posted}
    assert posted_names == {"alpha", "delta"}, f"Expected only registered set, got {posted_names}"


def test_force_metadata_update_intersects_by_repo_url(monkeypatch, capsys):
    """Match by repo_url even when local slug differs from Cortex's name."""
    from empirica.cli.command_handlers import projects_commands

    manifest = _make_manifest(
        ("local-alpha-renamed", "https://github.com/x/alpha"),  # repo matches Cortex
        ("local-beta", "https://github.com/x/beta-different"),  # no match
    )

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    cortex_rows = [{"name": "alpha-cortex-name", "repo_url": "https://github.com/x/alpha"}]

    args = SimpleNamespace(
        manifest_path=None, dry_run=False, force_metadata_update=True,
        cortex_url="https://cortex.example.com", api_key="sk-test",
        timeout=10.0, output="json", includes=None, excludes=None,
    )

    with patch.object(projects_commands, "load_manifest", return_value=manifest), \
         patch.object(projects_commands, "_fetch_cortex_collections", return_value=cortex_rows), \
         patch.object(projects_commands, "_register_one_project", side_effect=fake_register):
        projects_commands.handle_projects_bulk_register_command(args)

    posted_names = {p["name"] for p in posted}
    assert posted_names == {"local-alpha-renamed"}, f"Expected repo-url intersection, got {posted_names}"


def test_without_force_metadata_update_iterates_all(monkeypatch):
    """Without the flag, bulk-register iterates the full manifest (existing
    behavior — register new + skip existing as 409)."""
    from empirica.cli.command_handlers import projects_commands

    manifest = _make_manifest(
        ("alpha", None), ("beta", None), ("gamma", None),
    )

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    args = SimpleNamespace(
        manifest_path=None, dry_run=False, force_metadata_update=False,
        cortex_url="https://cortex.example.com", api_key="sk-test",
        timeout=10.0, output="json", includes=None, excludes=None,
    )

    # _fetch_cortex_collections should NOT be called when force_metadata_update=False
    with patch.object(projects_commands, "load_manifest", return_value=manifest), \
         patch.object(projects_commands, "_fetch_cortex_collections") as mock_fetch, \
         patch.object(projects_commands, "_register_one_project", side_effect=fake_register):
        projects_commands.handle_projects_bulk_register_command(args)
        mock_fetch.assert_not_called()

    posted_names = {p["name"] for p in posted}
    assert posted_names == {"alpha", "beta", "gamma"}


def test_force_metadata_update_empty_intersection_bails():
    """When the intersection is empty (none of the local projects are on
    Cortex), bail with a warning rather than POSTing nothing or all."""
    from empirica.cli.command_handlers import projects_commands

    manifest = _make_manifest(("alpha", None), ("beta", None))
    cortex_rows = [{"name": "totally-different"}]

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    args = SimpleNamespace(
        manifest_path=None, dry_run=False, force_metadata_update=True,
        cortex_url="https://cortex.example.com", api_key="sk-test",
        timeout=10.0, output="json", includes=None, excludes=None,
    )

    with patch.object(projects_commands, "load_manifest", return_value=manifest), \
         patch.object(projects_commands, "_fetch_cortex_collections", return_value=cortex_rows), \
         patch.object(projects_commands, "_register_one_project", side_effect=fake_register):
        projects_commands.handle_projects_bulk_register_command(args)

    assert posted == [], "Should bail without registering when intersection is empty"
