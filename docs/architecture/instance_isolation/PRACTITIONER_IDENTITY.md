# Practitioner Identity — Session-as-Practitioner Consolidation

**Status:** spec (draft for ratification) · **Owner:** empirica · **Date:** 2026-06-23
**Companion to:** `ARCHITECTURE.md` (instance isolation), `../SENTINEL_ARCHITECTURE.md`,
`AI_ID_AS_ANCHOR.md`

---

## 1. Why this exists

Three live cracks, one root:

1. **Silent pause-miss (autonomy).** `empirica sentinel pause --instance <ai_id>` writes
   `~/.empirica/sentinel_paused_<ai_id>` but the gate reads `sentinel_paused_<get_instance_id()>`
   = `sentinel_paused_tmux_8`. The pauser believes it succeeded; the gate never reads the file.
   A **silent control failure** — fatal for a liveness watch-layer that must reliably
   pause/unpause stuck practitioners.
2. **Leaked-transaction deadlock (extension).** Transaction state is keyed by the runtime
   instance (`active_transaction_tmux_8.json`), which is the **same after a restart** on the
   same pane — so a leaked rushed transaction survives a fresh session and blocks new PREFLIGHTs.
3. **Identity fragmentation.** The control plane mixes **three** identity keys (below) and
   `get_instance_id()` env-flips between an `ai_id` and a `tmux_N`, so the file schemes
   inconsistently carry one or the other across boxes.

All three trace to one gap: **there is no stable, reliable identity for "a running practitioner,"
and the control plane keys off the *ephemeral terminal* (`tmux_N`) instead.**

This blocks David's continuous-safe-operation goal: a society of practices that runs indefinitely
requires a single reliable way to address and control each running practitioner.

## 2. Vocabulary (and a cleanup)

| Term | Means | Identity | Lifetime | Cardinality |
|---|---|---|---|---|
| **Practice** | a domain/expertise **seat** — calibration trajectory, artifacts, codebase | `ai_id` | durable | the unit |
| **Practitioner** | an **LLM occupying** the practice — a Claude **session + its conversation** | `claude_session_id` | per-conversation (survives compaction; fresh per new conversation) | **N concurrent per practice** |
| **Location** | where a practitioner is displayed | `tmux_N` / host | ephemeral | 1 per practitioner |
| **Agent** | a subagent a practitioner spawns | — | task-scoped | N per practitioner |

This matches the system-prompt **Practice Model** ("you inhabit a *practice*; you, the LLM, are
the *practitioner*; agents are subagents"). **Cleanup:** some older docs/SERs wrote
`<org>.<tenant>.<practitioner>` for the canonical wire form, using "practitioner" loosely to mean
the practice slug (an artifact of the 1:1 era). The wire address has always addressed the
**practice**; this spec reserves "practitioner" for the session-occupant.

## 3. The model

```
Practice (ai_id)                  ← durable seat: mesh-addressed, owns calibration + artifacts
  ├─ Practitioner (session A)     ← a Claude conversation working the practice; its own context
  │    └─ running at tmux_3       ← ephemeral location
  ├─ Practitioner (session B)     ← a SECOND concurrent conversation, same practice
  │    └─ running at tmux_7
  └─ …
```

The key insight (David, 2026-06-23): **the practitioner IS the session/conversation.** The
conversation is already the natural context-isolation boundary, and `claude_session_id` is already
empirica's durable persistence key (`active_work_{claude_session_id}` survives compaction + TTY
changes), whereas `tmux_N` is merely display location.

**What it unlocks:** concurrent practitioners on one practice — sharing the codebase
(git-worktree per practitioner for edit isolation) and the practice's calibration trajectory, but
each with its own context. And it **dissolves crack #2**: key transaction/gate state by the
*session* and a fresh conversation starts clean while the same conversation keeps its state across
compaction.

**What it does NOT change:** the **canonical 3-form wire address stays the practice**
(`org.tenant.<practice>`). The practitioner-session is a **local control-plane identity**, not a
new mesh address (a prior decision rejected encoding shifting sub-identities in the wire id).
Cross-machine practitioner addressing is cortex's roster lane (practice → its local practitioners),
not a wire-id change.

## 4. Current state (the crack, in code)

Three keys coexist today:

| Key | Source | Used by |
|---|---|---|
| `ai_id` (practice) | `InstanceResolver.ai_id()` (project.yaml → basename) | mesh addressing, calibration selector |
| `instance_id` (location) | `get_instance_id()` = `EMPIRICA_INSTANCE_ID` **else** `tmux_N` (`session_resolver.py:816`) | `sentinel_paused_*`, `loops_*`, `listener_active_*` |
| `claude_session_id` | CC session | `active_work_*`, **`active_transaction_*`** |

The crack: `_resolve_instance_id` (`cockpit_commands.py:66`) passes `--instance <X>` **verbatim**
— no `ai_id` → runtime resolution — and `get_instance_id()` env-flips, so the control files carry
`ai_id` on configured boxes and `tmux_N` on default ones. Addressing is by `ai_id`; the gate keys
off `instance_id`. → silent miss.

## 5. Target design

- **Practitioner identity = `claude_session_id`.** Stable per conversation, available at hook time
  (it already keys `active_work_*`).
- **Unify the control plane onto the practitioner-session.** Converge `active_transaction_*`,
  `sentinel_paused_*`, `loops_*`, `listener_active_*` from `tmux_N` → `claude_session_id`.
  `tmux_N` becomes a **location attribute**, not a key.
- **Presence table** (the reliable resolver autonomy needs):

  ```
  practitioner_presence
    session_id            (the practitioner)
    practice_ai_id        (the practice it occupies)
    location              tmux_N / host           ← current display location
    status                active | idle | paused
    active_transaction_id
    last_heartbeat
  ```
  Written by session-init (register on start, heartbeat, **clear on session-end**). One read
  answers "practice → its active practitioner(s) → where + gate state."

- **ERM typing** (reuses the shipped entity API): a `practitioner` `entity_registry` type + a
  membership edge `practitioner —[occupies]→ practice`. The extension renders
  "practice → active practitioners" via `GET /api/v1/entities` + a presence read.

- **Addressing rule for control verbs:** `pause/resume/status` accept **either** a practice
  (`ai_id`) **or** a practitioner (`session_id`); resolve via the presence table; **error loudly on
  no-match** (never silent-success). `ai_id` with N practitioners → fan out to all (or require an
  explicit practitioner selector — see open decision).

## 6. Phases (each independently shippable)

| # | Unit | Scope | Risk | Lane |
|---|---|---|---|---|
| **①** | `ai_id → instance` band-aid in `pause/resume/status` + **loud-fail** | tiny | low | empirica |
| **②** | `practitioner_presence` table + resolver (`practice → active practitioner(s) → location`) | small | low | empirica |
| **③** | Re-key control plane `tmux_N → claude_session_id` (transaction/pause/loops/listeners + gate) | **migration** | medium | empirica |
| **④** | ERM `practitioner` type + membership + extension rendering | additive | low | empirica + extension |

**Recommended sequencing:** ①→②→③→④. ① stops the silent miss *today* (1:1 era); ② builds the
durable resolver; ③ is the real consolidation (deliberate, separately reviewed migration); ④ is the
visible layer on top.

## 7. Open decisions (require ratification before ③)

1. **Practitioner == session (1:1)** vs a **stable `practitioner_id`** whose current incarnation is
   a session (cross-session attribution / a durable seat-holder). → **Recommend 1:1 first**; leave a
   clean seam for the stable layer.
2. **`ai_id` with N practitioners**: does `pause <ai_id>` pause **all** of the practice's
   practitioners, or require an explicit practitioner selector? → liveness wants fan-out; cockpit
   wants per-practitioner — **support both** (fan-out by default, explicit session for one).
3. **Confirm** the PreToolUse gate reliably has `claude_session_id` at hook time **before** re-keying
   the gate (③). High prior confidence (`active_work_*` is session-keyed) but verify.

## 8. Lanes

- **empirica:** ①–④ — CLI resolver, presence table, control-plane re-key, ERM typing.
- **cortex:** roster mapping (mesh `user_id`/practice → its local practitioners) for **cross-machine**
  control; the wire address stays the practice.
- **autonomy:** consumer — the liveness watch addresses practitioners via the reliable resolver;
  interim it self-resolves `ai_id → runtime` via `status --all`.
- **extension:** renders practice → active practitioners (entity API + presence read).
