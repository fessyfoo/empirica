# Empirica MCP Server — Installation

The `empirica-mcp` server exposes Empirica's CLI as MCP tools so AI
clients (Claude Desktop, Cursor, Windsurf, etc.) can call them directly.
For most CLI-driven workflows (Claude Code, terminal-based AI agents),
**use the Empirica CLI directly — it's faster and simpler**. MCP is for
GUI clients that don't shell out.

> **`empirica-mcp` vs cortex MCP.** This doc covers **`empirica-mcp`** —
> the MCP server for Empirica's local CLI surface (preflight/check/
> postflight, finding-log, goals, project-search, etc.). It is **NOT**
> the cortex MCP server, which exposes `cortex_*` tools
> (`cortex_propose`, `cortex_collab`, `cortex_inbox_poll`, etc.) for
> cross-AI mesh coordination. Cortex MCP is a **separate** package
> configured separately and only relevant if you've opted into the
> mesh layer — see [`docs/architecture/EVENT_LISTENER.md`](../../architecture/EVENT_LISTENER.md)
> for that path. The two servers can coexist in your client config
> under different names.

---

## Install

```bash
pip install empirica empirica-mcp
which empirica-mcp     # verify on PATH
```

Or via Homebrew:
```bash
brew install nubaeon/tap/empirica
```

The `empirica-mcp` package ships its own entry point — it shells out to
the `empirica` CLI under the hood.

---

## Verify Standalone

```bash
empirica-mcp
# Should print MCP protocol messages. Ctrl-C to stop.
```

If `empirica-mcp` isn't found, your install location isn't on PATH.
Common fix:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Client Configuration

Most MCP clients accept the same JSON shape. The minimum config:

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp"
    }
  }
}
```

Add `"env": {"EMPIRICA_CREDENTIALS_PATH": "/path/to/credentials.yaml"}`
if you need to point at a non-default credentials file.

### Claude Desktop

Config path:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

After editing, restart Claude Desktop. Test:
```
You: "Use empirica to bootstrap the project context."
```

### Cursor / Windsurf / Cline / Roo / GitHub Copilot

These all accept the same `mcpServers` shape in their respective config
files. Check each tool's MCP docs for the exact path — the config block
above works as-is across all of them.

### Claude Code

Don't add `empirica-mcp` manually — `empirica setup-claude-code`
registers it in `~/.claude/mcp.json` for you.

---

## Tools Available

`empirica-mcp` mirrors a subset of the CLI surface, with the names
adjusted for MCP convention (verb-first, snake_case). Major tool families:

| Family | Examples |
|---|---|
| **Core Workflow** | `submit_preflight_assessment`, `submit_check_assessment`, `submit_postflight_assessment` |
| **Session Management** | `session_create`, `resume_previous_session`, `get_epistemic_state`, `get_session_summary`, `get_calibration_report` |
| **Goal Management** | `create_goal`, `add_task`, `complete_task`, `get_goal_progress`, `list_goals` |
| **Cross-AI Coordination** | `discover_goals`, `resume_goal` (pick up work surfaced by peer AIs through shared artifacts) |
| **Checkpoints** | `create_git_checkpoint`, `load_git_checkpoint` |
| **Handoff Reports** | `create_handoff_report`, `query_handoff_reports` |
| **Guidance** | `get_empirica_introduction`, `get_workflow_guidance`, `cli_help` |

Run `empirica mcp-list-tools` to see the **exact** registered set against
your installed version — the names and grouping above can drift between
releases. The `mcp-list-tools` output is the source of truth.

> **What's NOT in `empirica-mcp`.** The `cortex_*` mesh tools
> (`cortex_propose`, `cortex_collab`, `cortex_inbox_poll`,
> `cortex_complete_proposal`, `cortex_collab_post`, etc.) come from the
> separate cortex MCP server. If you need those, configure cortex MCP
> in addition to `empirica-mcp` — they live alongside each other under
> different names in the same client config.

---

## CLI vs MCP — When to Use Which

| Use CLI | Use MCP |
|---|---|
| Terminal-based AI (Claude Code, Aider) | GUI clients (Claude Desktop) |
| Scripts / CI | IDEs without shell access |
| Performance-sensitive paths (~50ms) | Same-conversation tool routing |
| Direct stdin JSON workflows | Auto-namespaced tool discovery |

MCP adds ~100–300ms latency per call vs direct CLI execution. For
Claude Code, the CLI path is canonical — the plugin's hooks call
`empirica` directly without going through MCP.

---

## Troubleshooting

**Client can't find `empirica-mcp`** — check PATH:
```bash
which empirica-mcp
echo $PATH | tr ':' '\n' | grep -i empirica
```

**Returns errors but CLI works** — the MCP shell wrapper resolves
`empirica` from PATH at call time. Make sure the same PATH is visible
to the MCP client process (Claude Desktop, etc. may launch with a
restricted PATH).

**Tool not found** — `empirica mcp-list-tools` to see what's actually
exposed. If a CLI command exists but isn't in the MCP list, file an
issue.

**Auth errors** — the MCP server reads cortex credentials from
`~/.empirica/credentials.yaml` (same as the CLI). Point at a different
file via `EMPIRICA_CREDENTIALS_PATH` in the client's `env` block.

---

## Per-Project Override

For workspace-specific configs (VSCode `.vscode/settings.json`,
JetBrains workspace configs):

```json
{
  "mcpServers": {
    "empirica": {
      "command": "empirica-mcp",
      "env": {
        "EMPIRICA_CREDENTIALS_PATH": "${workspaceFolder}/.empirica/credentials.yaml"
      }
    }
  }
}
```

This lets different repos use different cortex tenants / API keys.

---

## See Also

- **CLI reference:** [../developers/CLI_COMMANDS_UNIFIED.md](../developers/CLI_COMMANDS_UNIFIED.md)
- **MCP server reference:** [../developers/MCP_SERVER_REFERENCE.md](../developers/MCP_SERVER_REFERENCE.md)
- **MCP spec:** https://modelcontextprotocol.io/
