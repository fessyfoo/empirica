"""
ENP Setup Command — Initialize the Epistemic Network Protocol watcher.

Creates ~/.empirica/enp/ directories, copies config template, initializes
state from current repo HEAD, offers cron setup, registers hooks.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ENP_DIR = Path.home() / ".empirica" / "enp"
CONFIG_PATH = ENP_DIR / "config.json"
STATE_PATH = ENP_DIR / "state.json"


def _find_plugin_root() -> Path | None:
    """Find the empirica plugin root for accessing bundled scripts."""
    candidates = [
        Path.home() / ".claude" / "plugins" / "local" / "empirica",
        Path(__file__).parent.parent.parent / "plugins" / "claude-code-integration",
    ]
    for c in candidates:
        if (c / "scripts" / "enp-watcher.py").exists():
            return c
    return None


def _init_state_from_repos(config: dict) -> dict:
    """Initialize watcher state from current HEAD of each watched repo."""
    state = {}
    for watch in config.get("watch", []):
        repo = watch["repo"]
        remote = watch.get("remote", "origin")
        branch = watch.get("branch", "main")
        if not Path(repo).exists():
            print(f"  Warning: {repo} not found, skipping")
            continue
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                head = result.stdout.strip()
                state_key = f"{repo}:{remote}/{branch}"
                state[state_key] = head
                print(f"  Initialized {watch.get('label', repo)}: {head[:8]}")
        except Exception as e:
            print(f"  Warning: could not read HEAD for {repo}: {e}")
    return state


def _offer_cron(plugin_root: Path):
    """Print cron setup instructions."""
    watcher = plugin_root / "scripts" / "enp-watcher.py"
    print("\nTo run the watcher every 5 minutes, add to crontab (crontab -e):")
    print(f"  */5 * * * * python3 {watcher} >> ~/.empirica/enp/watcher.log 2>&1")


def _register_hooks(plugin_root: Path):
    """Print hook registration instructions."""
    notify = plugin_root / "hooks" / "enp-notify.py"
    postflight = plugin_root / "hooks" / "enp-postflight-notify.py"
    print("\nTo register ENP hooks, add to ~/.claude/settings.json hooks array:")
    print(f'  {{"type": "command", "event": "SessionStart", "command": "python3 {notify}"}}')
    print(f'  {{"type": "command", "event": "PostToolUse", "command": "python3 {postflight}"}}')


def handle_enp_setup_command(args):
    """Handle enp-setup command: initialize ENP watcher infrastructure."""
    print("ENP Setup — Epistemic Network Protocol Watcher")
    print("=" * 50)

    # 1. Create directories
    ENP_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Created {ENP_DIR}")

    # 2. Find plugin root
    plugin_root = _find_plugin_root()
    if not plugin_root:
        print("Error: Could not find empirica plugin with ENP scripts")
        return None

    # 3. Copy config template if no config exists
    if CONFIG_PATH.exists():
        print(f"Config already exists: {CONFIG_PATH}")
        print("  Edit it to add your watched repos and ntfy topics")
    else:
        example = plugin_root / "scripts" / "enp-config.example.json"
        if example.exists():
            shutil.copy2(example, CONFIG_PATH)
            print(f"Created config from template: {CONFIG_PATH}")
            print("  Edit this file to configure your watched repos")
        else:
            print(f"Warning: no config template found at {example}")

    # 4. Initialize state if config exists and has repos
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text())
            if config.get("watch"):
                print("\nInitializing watcher state from repo HEADs...")
                state = _init_state_from_repos(config)
                STATE_PATH.write_text(json.dumps(state, indent=2))
                print(f"  State saved: {STATE_PATH}")
            else:
                print("\n  No repos configured yet. Edit config.json to add watch entries.")
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read config: {e}")

    # 5. Cron and hooks instructions
    _offer_cron(plugin_root)
    _register_hooks(plugin_root)

    print("\n" + "=" * 50)
    print("ENP setup complete. Run the watcher manually to test:")
    print(f"  python3 {plugin_root / 'scripts' / 'enp-watcher.py'}")

    return None
