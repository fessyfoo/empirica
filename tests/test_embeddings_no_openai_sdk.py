"""Regression tests: the embeddings module must not depend on the openai SDK.

Empirica's default embeddings stack runs locally (ollama / qwen3-embedding).
The ``openai`` provider is opt-in and hits the REST ``/v1/embeddings`` endpoint
directly via ``requests`` — the same shape as the jina/voyage providers — so
the heavyweight ``openai`` SDK is no longer a dependency.

Diagnosed 2026-06-18: a top-level ``from openai import OpenAI`` in
``embeddings.py`` loaded the SDK (~0.5s) whenever the module was imported and
openai happened to be installed. That eager cost tripped #116's Python-3.11
``/health`` hot path. These tests lock the SDK back out and verify the openai
provider still works over plain HTTP.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

from empirica.core.qdrant.embeddings import EmbeddingsProvider


def test_importing_embeddings_does_not_pull_openai_sdk():
    """Importing the module must not drag in the openai SDK.

    Runs in a fresh subprocess so prior imports in this test session can't
    mask a stray eager import.
    """
    code = (
        "import empirica.core.qdrant.embeddings\n"
        "import sys\n"
        "assert 'openai' not in sys.modules, "
        "'embeddings.py eagerly imported the openai SDK'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_openai_provider_uses_rest_not_sdk():
    """The openai provider must embed via a requests.post to /v1/embeddings,
    carrying the API key as a bearer — with no openai client object."""
    env = {"EMPIRICA_EMBEDDINGS_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}
    with (
        patch.dict("os.environ", env, clear=False),
        patch("empirica.core.qdrant.embeddings._load_config_file", return_value={}),
    ):
        provider = EmbeddingsProvider()
        # No SDK client is instantiated — the provider talks REST.
        assert provider._client is None

        fake_resp = MagicMock()
        fake_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        fake_resp.raise_for_status.return_value = None

        with patch("requests.post", return_value=fake_resp) as mock_post:
            vec = provider.embed("hello world")

        assert vec == [0.1, 0.2, 0.3]
        assert mock_post.call_count == 1
        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        assert "api.openai.com/v1/embeddings" in url
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test"


def test_openai_provider_requires_api_key():
    """Missing OPENAI_API_KEY must fail fast at construction, not at embed time."""
    with (
        patch.dict("os.environ", {"EMPIRICA_EMBEDDINGS_PROVIDER": "openai"}, clear=False),
        patch("empirica.core.qdrant.embeddings._load_config_file", return_value={}),
    ):
        # Ensure no key leaks in from the ambient env.
        import os

        os.environ.pop("OPENAI_API_KEY", None)
        try:
            EmbeddingsProvider()
        except RuntimeError as e:
            assert "OPENAI_API_KEY" in str(e)
        else:
            raise AssertionError("expected RuntimeError when OPENAI_API_KEY is unset")
