"""CISA Known Exploited Vulnerabilities (KEV) feed — download, cache, lookup.

The KEV catalog is the actively-exploited subset of CVEs. A CVE in KEV means
real-world exploitation has been observed — much higher signal than CVE alone.

Free public JSON feed at:
    https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json

Updated daily by CISA. ~1,500 entries as of 2026-04. Schema documented at
https://www.cisa.gov/known-exploited-vulnerabilities (per-entry: cveID, dateAdded,
requiredAction, knownRansomwareCampaignUse, vendorProject, product, etc.).

This module fetches once per TTL (default 24h), caches to ~/.empirica/feeds/,
and exposes O(1) lookup by CVE ID.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
DEFAULT_TTL_SECONDS = 24 * 3600  # 24 hours


class KEVFeed:
    """CISA KEV catalog with local caching."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        url: str = CISA_KEV_URL,
    ) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self.cache_dir = cache_dir or (Path.home() / ".empirica" / "feeds")
        self.cache_path = self.cache_dir / "cisa_kev.json"
        self._index: dict[str, dict[str, Any]] | None = None

    def is_fresh(self) -> bool:
        """True if cache exists and is younger than ttl_seconds."""
        if not self.cache_path.exists():
            return False
        age = time.time() - self.cache_path.stat().st_mtime
        return age < self.ttl_seconds

    def refresh(self, force: bool = False) -> dict[str, Any]:
        """Download the catalog if stale (or force=True). Returns parsed JSON.

        Falls back to stale cache if download fails. Raises only when neither
        fresh download nor stale cache is available.
        """
        if not force and self.is_fresh():
            return self._read_cache()

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(self.url, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            tmp_path = self.cache_path.with_suffix(".json.tmp")
            tmp_path.write_text(payload)
            tmp_path.replace(self.cache_path)
            self._index = None  # invalidate
            logger.info(
                "KEV feed refreshed: %d entries (catalog %s)",
                len(data.get("vulnerabilities", [])),
                data.get("catalogVersion", "?"),
            )
            return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            if self.cache_path.exists():
                logger.warning(
                    "KEV refresh failed (%s) — using stale cache from %s",
                    exc,
                    time.strftime("%Y-%m-%d", time.localtime(self.cache_path.stat().st_mtime)),
                )
                return self._read_cache()
            raise RuntimeError(f"Cannot fetch KEV feed and no cache available: {exc}") from exc

    def _read_cache(self) -> dict[str, Any]:
        return json.loads(self.cache_path.read_text())

    def _build_index(self) -> dict[str, dict[str, Any]]:
        """Build CVE-ID → entry map from the catalog."""
        data = self._read_cache() if self.cache_path.exists() else self.refresh()
        return {entry["cveID"]: entry for entry in data.get("vulnerabilities", []) if "cveID" in entry}

    @property
    def index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def lookup(self, cve_id: str) -> dict[str, Any] | None:
        """Return KEV entry for a CVE ID, or None if not in KEV."""
        return self.index.get(cve_id)

    def lookup_many(self, cve_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch lookup. Returns {cve_id: entry} for matched CVEs only."""
        idx = self.index
        return {cve_id: idx[cve_id] for cve_id in cve_ids if cve_id in idx}

    def catalog_metadata(self) -> dict[str, Any]:
        """Return catalog version, release date, total entries."""
        data = self._read_cache() if self.cache_path.exists() else self.refresh()
        return {
            "catalog_version": data.get("catalogVersion"),
            "date_released": data.get("dateReleased"),
            "total_entries": len(data.get("vulnerabilities", [])),
            "cache_path": str(self.cache_path),
            "cache_age_hours": (
                (time.time() - self.cache_path.stat().st_mtime) / 3600 if self.cache_path.exists() else None
            ),
        }
