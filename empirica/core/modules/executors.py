"""Module install executors — the plugin-layer side of the two-layer install.

``fetch_module`` is the **auth-gated artifact pre-step** (leg 2): it stages a
module's distribution artifacts so the plugin layer can place them. The seat
layer (autonomy's ``install_seat.py``) runs between fetch and provision; it
never fetches — that's this module's job, keeping install_seat single-purpose.

Design invariants (from the converged module SER):
- **Write-by-default, ``--dry-run`` previews** — mirrors install_seat's contract,
  so the front-door can dry-run the whole ``fetch → seat → provision`` chain.
- **Idempotent** — re-fetching an already-staged/installed artifact is a no-op.
- **Per-step receipt** — every artifact reports its own status so a bulk
  front-door can see exactly which step needs attention.
- **Reference-only secrets** — a ``secrets_ref`` is resolved via the
  secrets-manager at runtime; a raw key never reaches this code (the manifest
  validator already rejects one at the schema layer).

The registry is config-driven (env), so this carries no fake infrastructure: a
``plugin_archive`` resolves from a local path (dev/test) or a configured
registry base URL; absent both, the step reports ``unconfigured`` rather than
crashing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path

from empirica.core.modules.manifest import ModuleManifest

STAGING_ROOT = Path.home() / ".empirica" / "module_staging"
REGISTRY_ENV = "EMPIRICA_MODULE_REGISTRY"  # base URL for plugin-archive fetch
INDEX_ENV = "EMPIRICA_MODULE_INDEX_URL"  # auth-gated pip index for python_packages


def _resolve_secret_ref(ref: str | None) -> tuple[str | None, str]:
    """Resolve a reference-only secret to its value (never a raw key in source).

    Returns ``(value, status)``. ``env:VAR`` reads the environment directly.
    ``<scheme>://...`` is resolved by the manager's CLI if present on PATH
    (``doppler``/``op``/``vault``), else reported ``manager_cli_absent`` — the
    caller degrades gracefully rather than failing the whole fetch.
    """
    if not ref:
        return None, "none"
    if ref.startswith("env:"):
        var = ref[len("env:") :]
        val = os.environ.get(var)
        return (val, "resolved") if val else (None, "env_unset")
    scheme = ref.split("://", 1)[0] if "://" in ref else ""
    if scheme and shutil.which(scheme):
        try:
            out = subprocess.run(
                [scheme, "secrets", "get", ref] if scheme == "doppler" else [scheme, "read", ref],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            val = out.stdout.strip()
            return (val, "resolved") if val else (None, "manager_empty")
        except (subprocess.SubprocessError, OSError):
            return None, "manager_error"
    return None, "manager_cli_absent"


def _pkg_installed(spec: str) -> bool:
    """True if the (possibly ``name==ver``/``name>=ver``) package is importable."""
    name = spec
    for sep in ("==", ">=", "<=", "~=", ">", "<", "!=", "["):
        if sep in name:
            name = name.split(sep, 1)[0]
    name = name.strip()
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def _pip_install(spec: str, index_url: str | None) -> tuple[bool, str]:
    """Install one package via the current interpreter's pip. Returns (ok, detail)."""
    cmd = [sys.executable, "-m", "pip", "install", spec]
    if index_url:
        cmd += ["--index-url", index_url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
        return True, "installed"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "pip failed").strip().splitlines()[-1][:300]
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)[:300]


def _fetch_archive(archive: str, dest: Path, registry_base: str | None, bearer: str | None) -> tuple[bool, str]:
    """Stage ``archive`` to ``dest``. Local path → copy; else registry+bearer → download."""
    local = Path(archive)
    if local.exists():
        shutil.copy2(local, dest)
        return True, "copied_local"
    if not registry_base:
        return False, "no_registry_configured"
    url = registry_base.rstrip("/") + "/" + archive
    req = urllib.request.Request(url)
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        return True, f"downloaded:{url}"
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)[:300]


def fetch_module(
    manifest: ModuleManifest,
    *,
    dry_run: bool = False,
    staging_root: Path | None = None,
    registry_base: str | None = None,
    index_url: str | None = None,
) -> dict:
    """Stage a module's distribution artifacts (python_packages + plugin_archive).

    Returns a receipt: ``{ok, action, module, dry_run, staged_path, steps, errors}``
    where each step carries its own ``status`` (``would_*`` under dry-run).
    """
    root = staging_root or STAGING_ROOT
    registry_base = registry_base if registry_base is not None else os.environ.get(REGISTRY_ENV)
    index_url = index_url if index_url is not None else os.environ.get(INDEX_ENV)

    staged = root / manifest.name
    steps: list[dict] = []
    errors: list[str] = []

    if not dry_run:
        staged.mkdir(parents=True, exist_ok=True)

    # python_packages → pip (idempotent: skip if already importable)
    for spec in manifest.artifacts.python_packages:
        if _pkg_installed(spec):
            steps.append({"kind": "python_package", "target": spec, "status": "skipped", "detail": "already installed"})
            continue
        if dry_run:
            steps.append({"kind": "python_package", "target": spec, "status": "would_install", "detail": "pip install"})
            continue
        ok, detail = _pip_install(spec, index_url)
        steps.append(
            {"kind": "python_package", "target": spec, "status": "installed" if ok else "error", "detail": detail}
        )
        if not ok:
            errors.append(f"python_package {spec}: {detail}")

    # plugin_archive → local copy or registry download (idempotent: skip if staged)
    archive = manifest.artifacts.plugin_archive
    if archive:
        dest = staged / Path(archive).name
        if dest.exists():
            steps.append({"kind": "plugin_archive", "target": archive, "status": "skipped", "detail": "already staged"})
        elif dry_run:
            src = "local" if Path(archive).exists() else (f"{registry_base}" if registry_base else "unconfigured")
            status = "unconfigured" if src == "unconfigured" else "would_fetch"
            steps.append({"kind": "plugin_archive", "target": archive, "status": status, "detail": src})
        else:
            bearer, sref_status = _resolve_secret_ref(manifest.requires_runtime.secrets_ref)
            need_remote = not Path(archive).exists()
            if need_remote and not registry_base:
                steps.append(
                    {"kind": "plugin_archive", "target": archive, "status": "unconfigured", "detail": "no registry"}
                )
            elif need_remote and manifest.requires_runtime.secrets_ref and bearer is None:
                steps.append(
                    {
                        "kind": "plugin_archive",
                        "target": archive,
                        "status": "unresolved_secret",
                        "detail": sref_status,
                    }
                )
                errors.append(f"plugin_archive {archive}: secret {sref_status}")
            else:
                ok, detail = _fetch_archive(archive, dest, registry_base, bearer)
                steps.append(
                    {
                        "kind": "plugin_archive",
                        "target": archive,
                        "status": "fetched" if ok else "error",
                        "detail": detail,
                    }
                )
                if not ok:
                    errors.append(f"plugin_archive {archive}: {detail}")

    return {
        "ok": not errors,
        "action": "fetch",
        "module": manifest.name,
        "dry_run": dry_run,
        "staged_path": str(staged),
        "steps": steps,
        "errors": errors,
    }
