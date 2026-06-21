"""Tests for project-bootstrap mesh-agreements sync (trigger #1 of the
MESH_SHARING_AGREEMENTS.md sync contract).

Wires ``empirica.core.mesh_sharing.sync_from_cortex`` into
``project_bootstrap.handle_project_bootstrap_command`` as a non-fatal step
so every session-start refreshes the local entity_registry mirror.

Coverage:
1. Sync runs when cortex creds are present; uses returned SyncResult.
2. Sync silently skips when cortex creds are missing (e.g. offline-first
   install).
3. Sync logs a debug message on cortex transport error without raising.
4. Sync logs a debug message on any unexpected exception without raising.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


def test_sync_runs_when_creds_present(caplog):
    """Happy path: creds resolve → WorkspaceDBRepository.open is called,
    sync_from_cortex result logged at INFO."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    mock_result = MagicMock()
    mock_result.error = None
    mock_result.added = 2
    mock_result.updated = 1
    mock_result.marked_revoked = 0

    fake_repo = MagicMock()
    fake_repo.__enter__ = MagicMock(return_value=fake_repo)
    fake_repo.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch(
            "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
            return_value=fake_repo,
        ),
        patch(
            "empirica.core.mesh_sharing.sync_from_cortex",
            return_value=mock_result,
        ) as mock_sync,
        caplog.at_level(logging.INFO, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {
            "url": "https://example.com",
            "api_key": "ctx_test_key",
        }
        _sync_mesh_sharing_agreements()

    mock_sync.assert_called_once()
    # Repo + url + key passed through
    args, _kwargs = mock_sync.call_args
    assert args[0] is fake_repo
    assert args[1] == "https://example.com"
    assert args[2] == "ctx_test_key"
    # Result counts logged
    assert any("2 added" in r.message and "1 updated" in r.message for r in caplog.records)


def test_sync_skips_when_creds_missing(caplog):
    """No cortex url/key in config → skip with debug log, no exception."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch("empirica.core.mesh_sharing.sync_from_cortex") as mock_sync,
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {}
        _sync_mesh_sharing_agreements()

    mock_sync.assert_not_called()
    assert any("creds missing" in r.message for r in caplog.records)


def test_sync_transport_error_is_non_fatal(caplog):
    """sync_from_cortex returns SyncResult(error='...') → debug log, no
    exception."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    mock_result = MagicMock()
    mock_result.error = "fetch failed: HTTP 503"
    mock_result.added = 0
    mock_result.updated = 0
    mock_result.marked_revoked = 0

    fake_repo = MagicMock()
    fake_repo.__enter__ = MagicMock(return_value=fake_repo)
    fake_repo.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch(
            "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
            return_value=fake_repo,
        ),
        patch(
            "empirica.core.mesh_sharing.sync_from_cortex",
            return_value=mock_result,
        ),
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {
            "url": "https://example.com",
            "api_key": "ctx_key",
        }
        _sync_mesh_sharing_agreements()  # must not raise

    assert any("HTTP 503" in r.message for r in caplog.records)


def test_sync_unexpected_exception_is_swallowed(caplog):
    """If any underlying call throws (DB locked, import error, etc.),
    bootstrap continues — _sync swallows + debug logs."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
            side_effect=RuntimeError("simulated import error"),
        ),
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        _sync_mesh_sharing_agreements()  # must not raise

    assert any("skipped" in r.message and "simulated" in r.message for r in caplog.records)
