"""`empirica forgejo-publish` — provision a managed Forgejo remote for a project.

This is the operator / self-hosting-power-user provisioning verb (NOT an
end-user default flow). It drives the PUSH provisioning mode: Forgejo becomes a
git remote for the project and the local repo is pushed up. That's the right
mode for a project with **no existing remote** (e.g. empirica-mesh-support) —
Forgejo's managed pull-mirror can't apply when there's no origin to pull from.

Flow:
  1. POST /v1/projects/{id}/forgejo-publish (cortex, owner-scoped) → returns
     {forgejo_repo_url, deploy_key_private (once), refspecs[], ...}
  2. Write deploy_key_private 0600 to ~/.config/empirica/forgejo-keys/<uuid>
  3. `git remote add forgejo <url>` (or set-url if it already exists)
  4. Push each cortex-supplied refspec via GIT_SSH_COMMAND using the deploy key

repo_url stays pinned to `origin`: adding the `forgejo` remote must NOT become
the project's canonical repo_url (that's live-read from origin elsewhere).
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

FORGEJO_PUBLISH_PATH = "/v1/projects/{project_id}/forgejo-publish"
FORGEJO_REMOTE_NAME = "forgejo"


def _resolve_cortex_config() -> tuple[str | None, str | None]:
    """(cortex_url, api_key) from ~/.empirica/credentials.yaml `cortex.*`."""
    try:
        cred = Path.home() / ".empirica" / "credentials.yaml"
        if not cred.exists():
            return None, None
        cfg = yaml.safe_load(cred.read_text()) or {}
        cortex = cfg.get("cortex") or {}
        return cortex.get("url"), cortex.get("api_key")
    except Exception:
        return None, None


def _resolve_project(path: Path) -> tuple[str | None, str | None]:
    """(project_id, name) from <path>/.empirica/project.yaml. (None, None) on miss."""
    try:
        py = path / ".empirica" / "project.yaml"
        if not py.exists():
            return None, None
        cfg = yaml.safe_load(py.read_text()) or {}
        return cfg.get("project_id"), cfg.get("name") or cfg.get("ai_id")
    except Exception:
        return None, None


def _forgejo_publish_post(
    cortex_url: str, project_id: str, api_key: str, *,
    rotate: bool = False, description: str | None = None, timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """POST the forgejo-publish endpoint. Returns (status, parsed_body)."""
    url = cortex_url.rstrip("/") + FORGEJO_PUBLISH_PATH.format(project_id=project_id)
    body: dict[str, Any] = {}
    if rotate:
        body["rotate"] = True
    if description:
        body["description"] = description
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


def _write_deploy_key(project_uuid: str, key_material: str) -> Path:
    """Write the deploy key 0600 under ~/.config/empirica/forgejo-keys/<uuid>."""
    key_dir = Path.home() / ".config" / "empirica" / "forgejo-keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / project_uuid
    # ssh requires a trailing newline on the private key file.
    text = key_material if key_material.endswith("\n") else key_material + "\n"
    key_path.write_text(text)
    key_path.chmod(0o600)
    return key_path


def _git(project_path: Path, *args: str, key_path: Path | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    """Run git in `project_path`, optionally pinning the ssh key for pushes."""
    env = dict(os.environ)
    if key_path is not None:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
    return subprocess.run(
        ["git", "-C", str(project_path), *args],
        capture_output=True, text=True, timeout=timeout, env=env, check=False,
    )


def _set_forgejo_remote(project_path: Path, url: str) -> None:
    """Add the `forgejo` remote, or update its URL if it already exists.

    Never touches `origin` — repo_url stays pinned to the canonical remote.
    """
    existing = _git(project_path, "remote")
    remotes = (existing.stdout or "").split()
    if FORGEJO_REMOTE_NAME in remotes:
        _git(project_path, "remote", "set-url", FORGEJO_REMOTE_NAME, url)
    else:
        _git(project_path, "remote", "add", FORGEJO_REMOTE_NAME, url)


def handle_forgejo_publish_command(args) -> int:
    """Provision a managed Forgejo remote + push the project to it."""
    output_json = getattr(args, "output", "human") == "json"
    project_path = Path(getattr(args, "path", ".") or ".").resolve()

    def _emit(payload: dict[str, Any], code: int) -> int:
        if output_json:
            print(json.dumps(payload, indent=2))
        else:
            if payload.get("ok"):
                print(f"✅ Forgejo provisioned: {payload.get('forgejo_repo_url')}")
                for ref, ok in (payload.get("push_results") or {}).items():
                    print(f"   {'✓' if ok else '✗'} {ref}")
                if payload.get("key_path"):
                    print(f"   deploy key: {payload['key_path']} (0600)")
            else:
                print(f"❌ forgejo-publish: {payload.get('error') or payload.get('reason')}")
        return code

    project_id, _ = _resolve_project(project_path)
    if not project_id:
        return _emit({"ok": False, "reason": f"no .empirica/project.yaml at {project_path}"}, 1)

    cortex_url, api_key = _resolve_cortex_config()
    if not (cortex_url and api_key):
        return _emit({"ok": False, "reason": "no cortex url/api_key (configure ~/.empirica/credentials.yaml)"}, 1)

    status, body = _forgejo_publish_post(
        cortex_url, project_id, api_key,
        rotate=getattr(args, "rotate", False),
        description=getattr(args, "description", None),
    )
    if status != 200:
        return _emit({"ok": False, "reason": f"cortex {status}", "error": body.get("error"), "body": body}, 1)

    forgejo_url = body.get("forgejo_repo_url")
    refspecs = body.get("refspecs") or []
    key_material = body.get("deploy_key_private")
    if not forgejo_url:
        return _emit({"ok": False, "reason": "cortex response missing forgejo_repo_url", "body": body}, 1)

    # Idempotent re-call without rotate returns no key — can't (re)push without one.
    if not key_material:
        return _emit({
            "ok": True, "forgejo_repo_url": forgejo_url,
            "already_published": bool(body.get("already_published")),
            "note": "no deploy key returned (already published) — re-run with --rotate to mint a fresh key and re-push",
        }, 0)

    key_path = _write_deploy_key(project_id, key_material)
    _set_forgejo_remote(project_path, forgejo_url)

    push_results: dict[str, bool] = {}
    for spec in refspecs:
        r = _git(project_path, "push", FORGEJO_REMOTE_NAME, spec, key_path=key_path)
        push_results[spec] = r.returncode == 0
        if r.returncode != 0 and not output_json:
            print(f"   ✗ push {spec}: {(r.stderr or '').strip()[:200]}")

    ok = all(push_results.values()) if push_results else True
    return _emit({
        "ok": ok,
        "forgejo_repo_url": forgejo_url,
        "refspecs": refspecs,
        "push_results": push_results,
        "key_path": str(key_path),
    }, 0 if ok else 1)
