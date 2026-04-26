---
name: loop-cron
description: "Use when scheduling cron-mode loops with Claude Code's /loop, when registering periodic background work, when the user says 'cron loop', 'periodic loop', 'register a cron', 'schedule recurring work', or when configuring a loop that needs to be visible in `empirica status`. This skill provides the prompt template that wires CC's /loop into Empirica's loop registry — register at start, check pause flag each fire, heartbeat at end. Without this wiring, a /loop cron is invisible to the cockpit and uncontrollable from any other terminal."
version: 1.0.0
---

# Loop-Cron Wiring

Claude Code's built-in `/loop` skill drives periodic work via `CronCreate`.
On its own, those loops are **invisible to other terminals** — you can't
see what's scheduled, can't tell when one last ran, and can't pause them
without killing the process. Empirica's loop registry fixes that with
three CLI calls layered into the cron prompt.

This skill is the prompt template for cron-mode loops that integrates with
the registry.

---

## When to Use

You're about to start a cron-mode loop with `/loop` (or already have one
running) and:

- You want it visible in `empirica status --all`
- You want to pause/resume from any terminal without killing the process
- You want a heartbeat trail for "did it actually run, did it succeed"
- You want it to survive a Claude Code session restart cleanly

If your loop is one-shot or interactive ("dynamic mode"), skip this — it
applies to **scheduled** (cron/interval) loops only.

---

## Cron Prompt Template

When invoking `/loop` in cron mode, prepend these three CLI lines to your
task prompt. The variables are: `{NAME}` (loop identifier, no spaces),
`{CRON}` (cron expression), `{DESC}` (one-line description).

```
At start (idempotent — safe to call every fire):
  empirica loop register --name {NAME} --kind cron --cron "{CRON}" --description "{DESC}"

Check pause (exit silently if paused):
  if [ "$(empirica loop status {NAME} --output json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("paused"))')" = "True" ]; then
    exit 0
  fi

[... your actual work here ...]

At end:
  empirica loop heartbeat {NAME} --status ok --message "{summary of what happened}"

On failure:
  empirica loop heartbeat {NAME} --status fail --message "{error}"
```

The register call is **idempotent** — it preserves `last_run`,
`last_status`, and `last_message` while updating the schedule. So you
can re-issue it on every fire without losing history.

---

## Concrete Example

A 15-minute inbox poll:

```
/loop --cron "*/15 * * * *" --prompt "
At start:
  empirica loop register --name inbox-poll --kind cron --cron '*/15 * * * *' --description 'ECO inbox digest'

Check pause:
  if [ \"\$(empirica loop status inbox-poll --output json | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"paused\"))')\" = \"True\" ]; then
    exit 0
  fi

Then: read each connected inbox, summarize unread mail, capture findings.

At end:
  empirica loop heartbeat inbox-poll --status ok --message \"\$INBOXES_CHECKED inboxes checked, \$NEW_COUNT new\"

On failure:
  empirica loop heartbeat inbox-poll --status fail --message \"\$ERROR_MESSAGE\"
"
```

---

## Pausing and Resuming

From any terminal, on any machine that shares `~/.empirica/`:

```
empirica loop pause inbox-poll      # next fire exits early
empirica loop resume inbox-poll     # next fire runs normally
empirica loop status inbox-poll     # show last run + paused state
empirica loop list                  # all loops on this instance
empirica loop unregister inbox-poll # remove from registry entirely
```

The pause check is a single fast file-stat (`~/.empirica/loop_paused_{instance_id}_{name}`),
so the cost of the gate is negligible even for high-frequency loops.

---

## Visibility

Once registered, the loop appears in:

```
empirica status              # current instance
empirica status --all        # every Claude across every terminal
watch -n 2 empirica status --all --pretty   # live cockpit
```

…showing kind, schedule, last fire, last status, and pause state in one
table. This is the comprehension principle: you shouldn't run loops you
can't read.

---

## Migration from Bespoke Pause Files

Old patterns like `touch /tmp/inbox-poll.disabled` should migrate to
`empirica loop pause inbox-poll`. The standard pattern wins because:

- Visible to other instances (any tmux pane can pause/resume)
- Surfaces in the cockpit (`empirica status` shows pause state)
- Survives reboots (`~/.empirica/` persists)
- Scoped per-instance (no cross-talk between Claudes)

Once an old `/tmp/*.disabled` loop is re-issued through this template,
the bespoke flag file becomes dead code and can be deleted.

---

## Related

- `empirica status --all --pretty` — cockpit overview
- `empirica sentinel pause/resume` — per-instance noetic firewall control
- `docs/architecture/COCKPIT.md` — full state-file layout and discovery rules
