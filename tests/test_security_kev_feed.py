"""Tests for empirica.core.security.kev_feed.

KEVFeed handles download, caching, and CVE lookup against the CISA Known
Exploited Vulnerabilities catalog. Tests use a stub HTTP fixture rather than
hitting the real CISA endpoint.
"""

from __future__ import annotations

import json
import time

import pytest

from empirica.core.security.kev_feed import KEVFeed

SAMPLE_CATALOG = {
    "title": "CISA Catalog of Known Exploited Vulnerabilities",
    "catalogVersion": "2026.04.24",
    "dateReleased": "2026-04-24T00:00:00.000Z",
    "count": 3,
    "vulnerabilities": [
        {
            "cveID": "CVE-2025-12345",
            "vendorProject": "ExampleCorp",
            "product": "ExampleProduct",
            "vulnerabilityName": "ExampleProduct RCE",
            "dateAdded": "2026-04-15",
            "shortDescription": "Remote code execution.",
            "requiredAction": "Apply patch.",
            "dueDate": "2026-05-06",
            "knownRansomwareCampaignUse": "Known",
            "cwes": ["CWE-78"],
        },
        {
            "cveID": "CVE-2024-99999",
            "vendorProject": "OtherCorp",
            "product": "OtherProduct",
            "vulnerabilityName": "OtherProduct auth bypass",
            "dateAdded": "2024-12-10",
            "shortDescription": "Auth bypass.",
            "requiredAction": "Upgrade.",
            "dueDate": "2024-12-31",
            "knownRansomwareCampaignUse": "Unknown",
            "cwes": ["CWE-287"],
        },
        {
            "cveID": "CVE-2023-11111",
            "vendorProject": "OldCorp",
            "product": "OldThing",
            "vulnerabilityName": "OldThing path traversal",
            "dateAdded": "2023-06-01",
            "shortDescription": "Path traversal.",
            "requiredAction": "Upgrade.",
            "dueDate": "2023-07-01",
            "knownRansomwareCampaignUse": "Unknown",
            "cwes": ["CWE-22"],
        },
    ],
}


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """KEVFeed with isolated cache dir and a stubbed urlopen."""
    payload = json.dumps(SAMPLE_CATALOG).encode("utf-8")

    class _StubResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(url, timeout=None):
        return _StubResponse(payload)

    monkeypatch.setattr("empirica.core.security.kev_feed.urllib.request.urlopen", fake_urlopen)
    return KEVFeed(cache_dir=tmp_path)


def test_refresh_downloads_and_caches(feed, tmp_path):
    data = feed.refresh(force=True)
    assert data["catalogVersion"] == "2026.04.24"
    assert (tmp_path / "cisa_kev.json").exists()


def test_is_fresh_after_refresh(feed):
    feed.refresh(force=True)
    assert feed.is_fresh()


def test_is_fresh_false_when_cache_missing(tmp_path):
    feed = KEVFeed(cache_dir=tmp_path)
    assert not feed.is_fresh()


def test_lookup_match(feed):
    feed.refresh(force=True)
    entry = feed.lookup("CVE-2025-12345")
    assert entry is not None
    assert entry["vendorProject"] == "ExampleCorp"
    assert entry["knownRansomwareCampaignUse"] == "Known"


def test_lookup_miss(feed):
    feed.refresh(force=True)
    assert feed.lookup("CVE-9999-NOTREAL") is None


def test_lookup_many_returns_only_matches(feed):
    feed.refresh(force=True)
    matches = feed.lookup_many(
        [
            "CVE-2025-12345",
            "CVE-2024-99999",
            "CVE-9999-NOTREAL",
        ]
    )
    assert set(matches.keys()) == {"CVE-2025-12345", "CVE-2024-99999"}


def test_catalog_metadata_after_refresh(feed):
    feed.refresh(force=True)
    meta = feed.catalog_metadata()
    assert meta["catalog_version"] == "2026.04.24"
    assert meta["total_entries"] == 3
    assert meta["cache_age_hours"] is not None
    assert meta["cache_age_hours"] >= 0


def test_refresh_falls_back_to_stale_cache(tmp_path, monkeypatch):
    """If the network fails but a cache exists, return the cached data."""
    cache_dir = tmp_path
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / "cisa_kev.json"
    cache.write_text(json.dumps(SAMPLE_CATALOG))

    # Force the cache to look stale by setting mtime far in the past.
    old_time = time.time() - (48 * 3600)
    import os

    os.utime(cache, (old_time, old_time))

    def fake_urlopen(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr("empirica.core.security.kev_feed.urllib.request.urlopen", fake_urlopen)

    feed = KEVFeed(cache_dir=cache_dir, ttl_seconds=24 * 3600)
    assert not feed.is_fresh()
    data = feed.refresh()  # Should fall back to stale cache rather than raise.
    assert data["catalogVersion"] == "2026.04.24"


def test_refresh_raises_when_no_cache_and_network_fails(tmp_path, monkeypatch):
    def fake_urlopen(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr("empirica.core.security.kev_feed.urllib.request.urlopen", fake_urlopen)

    feed = KEVFeed(cache_dir=tmp_path)
    with pytest.raises(RuntimeError, match="Cannot fetch KEV"):
        feed.refresh()


def test_index_lazy_load(feed):
    feed.refresh(force=True)
    # Touching .index twice should not re-build (just a property smoke test)
    idx1 = feed.index
    idx2 = feed.index
    assert idx1 is idx2
    assert "CVE-2025-12345" in idx1


def test_ttl_skip_when_fresh(feed, monkeypatch, tmp_path):
    """If cache is fresh, refresh() should not download again."""
    feed.refresh(force=True)
    call_count = {"n": 0}

    def counting_urlopen(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("Should not be called when cache is fresh")

    monkeypatch.setattr(
        "empirica.core.security.kev_feed.urllib.request.urlopen",
        counting_urlopen,
    )
    feed2 = KEVFeed(cache_dir=tmp_path)
    feed2.refresh()  # Not forced; should hit cache.
    assert call_count["n"] == 0
