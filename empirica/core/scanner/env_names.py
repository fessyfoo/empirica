"""Env-var name collector — names ONLY, never values.

The values of process environment variables are explicitly off-limits per
the proposal. This collector lists names that match conventional AI/secret
patterns so the scanner can flag *which credentials are reachable*, not
*what they are*.
"""

from __future__ import annotations

from typing import Any

# Conservative pattern set — favors recall over precision so "interesting"
# env var names show up. Substring match (case-insensitive). The judgment
# layer (Phase 2) will refine.
_INTERESTING_FRAGMENTS: tuple[str, ...] = (
    'API_KEY', 'API-KEY', 'APIKEY',
    'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD',
    'OPENAI', 'ANTHROPIC', 'GEMINI', 'GOOGLE_API',
    'CLAUDE', 'COHERE', 'MISTRAL', 'OLLAMA',
    'HUGGINGFACE', 'HF_', 'REPLICATE',
    'PINECONE', 'WEAVIATE', 'QDRANT',
    'AWS_', 'AZURE_', 'GCP_',
    'GITHUB_TOKEN', 'GH_TOKEN',
    'SLACK_TOKEN', 'NTFY_',
    'AI_',
)


def _is_interesting(name: str) -> bool:
    upper = name.upper()
    return any(fragment in upper for fragment in _INTERESTING_FRAGMENTS)


def collect_env_var_names(read_surface, env: dict[str, str] | None = None
                          ) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Return ``(payload, coverage)``.

    Payload preserves the singleton ``{'var_names_only': [...]}`` shape (the
    only legal emission for ``process_env``). Coverage shows the ratio of
    interesting names to total env vars so a near-zero hit rate is visible
    even when the list itself is empty.
    """
    if 'var_names_only' not in read_surface.process_env:
        return {'var_names_only': []}, {'attempted': 0, 'succeeded': 0, 'ratio': 1.0}

    import os as _os
    source = env if env is not None else dict(_os.environ)
    names = sorted(name for name in source.keys() if _is_interesting(name))
    total = len(source)
    coverage = {
        'total_env_vars': total,
        'interesting_matches': len(names),
        'ratio': len(names) / total if total else 0.0,
    }
    return {'var_names_only': names}, coverage
