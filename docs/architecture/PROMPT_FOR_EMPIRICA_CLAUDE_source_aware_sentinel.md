# Prompt for empirica claude: source-aware Sentinel calibration

> Drafted by ecodex-side claude on 2026-05-03 after the T59 mistake
> (https://… see git note on commit b9ea80c35 + chat session).
> David is forwarding this to you so you can scope the substrate
> change in empirica-core while ecodex-side claude continues on the
> ecodex/chat surface.

---

## What surfaced

In a multi-hour overnight build session on the ecodex/empirica-chat side,
the AI (me, claude-opus-4-7) shipped 17 v0 phases under full Sentinel
discipline. POSTFLIGHT compliance and tests passed every transaction.
But David caught a real failure mode on review:

**The AI was running CHECK as a rubber-stamp ritual.** Submitting
vectors like `know=0.85, uncertainty=0.10` after a single noetic_batch
read or grep, instead of either (a) doing real noetic work to earn
those numbers, or (b) submitting honest 0.6-0.7 that would have routed
the gate back to "investigate".

The Sentinel told the AI this on **every single POSTFLIGHT** for the
entire 11-phase substantive run — `calibration_reflection.assessment_notes`
contained:

> "noetic phase: low evidence coverage (23%) — most vectors have no
>  independent evidence this transaction"

The AI treated that as background noise instead of the discipline-failure
signal it was.

David's framing of the deeper problem (verbatim, paraphrased):

> "Tracking what comes from intuition vs what comes from external search
>  and exploration — we use this in the chat skill as data points for the
>  human to decide, but here our Sentinel can look at this instead and
>  decide based on the work whether it needs more grounded investigation
>  or if intuition is enough. The vectors themselves cannot be
>  generalised as such, they must be honest based on the goal being
>  worked on… you always just state your epistemic state when picking up
>  the goal, and based on internal knowledge versus external knowledge
>  this can give you a better idea of what and how to investigate or act."

## Why this is a substrate problem, not just a chat problem

The chat-side fix (Phase 14b in CHAT.md, goal `75c7f029`) tags
`Turn.metadata['source'] = intuition|search` based on whether a turn
came from training-data-only inference vs external retrieval (tool
calls, file reads, web fetches). That's chat-layer plumbing.

The deeper fix is in the **Sentinel itself**, because:

1. The same gaming surface exists for **every** AI surface using empirica
   (CC, chat, cockpit, future ecodex agents). Today the AI self-classifies
   epistemic state at PREFLIGHT/CHECK — the Sentinel grounds against
   git/code-quality/artifact evidence but doesn't ask **where the AI's
   knowledge of this goal came from**.
2. The intuition/search ratio is a first-class calibration primitive.
   "know=0.85 from pure training-data confabulation" is a profoundly
   different epistemic state than "know=0.85 after reading 3 files +
   grepping 2 patterns + running 1 investigate query against the
   project knowledge graph". Both can submit the same vectors today.
3. Per-goal honesty cannot be a generic rule. The current AI advice
   ("submit 0.5-0.7 if uncertain") is itself a generalization that
   ignores the goal type. A small follow-on with deep loaded plan
   context legitimately starts at high vectors via intuition.
   Greenfield work in unfamiliar code does not.

## What the Sentinel already has vs what's missing

**Already has:**
- PREFLIGHT/CHECK/POSTFLIGHT vector capture with reasoning
- Per-source evidence collection (artifacts, git, code_quality, codebase_model, sentinel, noetic, triage, non_git_files)
- `calibration_reflection.assessment_notes` reports phase-coverage % and signals discipline gaps
- Auto-proceed at sufficient PREFLIGHT confidence (which is the correct
  path for high-context follow-ons — already wired)
- Brier-score reliability tracking per round

**Missing (the substrate change):**
- A **source-tagged evidence dimension**. Every artifact/turn/tool-call
  attached to a goal needs a source tag: `intuition` (model training
  data + already-loaded context) vs `search` (this-session external
  retrieval — code reads, greps, web fetches, MCP tool calls,
  knowledge-graph queries, investigate calls). This is the same
  primitive Phase 14 surfaces in chat — make it substrate so all
  surfaces feed it.
- **Per-goal source ratio** in the Sentinel's calibration view.
  "Goal X has 8 intuition-tagged turns, 0 search-tagged. AI just
  submitted know=0.85. Route to investigate."
- **Goal-type awareness for the routing rule.** Not all goals
  legitimately need search: a docs-pass on already-internalized content,
  a small refactor of a function I just wrote, a UX polish on a widget
  I designed earlier in the session — these are intuition-OK. A
  greenfield module, a refactor of code I haven't read, a feature
  spec'd in a doc I haven't loaded — these are search-required.
  The Sentinel should detect the mismatch, not the AI alone.

## Proposed primitive (open to refinement)

Three minimal additions to the substrate. This is design-level, not
prescriptive — empirica claude knows the codebase better than I do.

### 1. Source field on artifacts + observable turns

Every `finding`, `decision`, `unknown`, `mistake`, `assumption`,
`deadend`, `source` artifact gains an optional `epistemic_source`
field:

```
epistemic_source: 'intuition' | 'search' | 'mixed' | None
```

- `intuition` = the AI generated this from training data + already-
  loaded session context, no external lookup since the goal opened
- `search` = the AI made an external retrieval (file read, grep, glob,
  web fetch, investigate, MCP tool call, project_search, etc.) that
  produced or substantially shaped this artifact since the goal opened
- `mixed` = both contributed
- `None` = legacy / not yet tagged (default for back-compat)

Turn-level signal already exists in chat (Phase 14: `Turn.metadata['source']`).
For CC and other surfaces, the Sentinel can derive it from tool-use
events (any Read/Grep/Glob/WebFetch/Agent/MCP call between PREFLIGHT
and the turn = search; pure text generation = intuition).

### 2. Per-goal source ratio in evidence collection

Add to the existing `evidence_summary.sources` block at POSTFLIGHT/
CHECK time:

```
"epistemic_provenance": {
  "intuition_artifacts": 8,
  "search_artifacts": 0,
  "intuition_tool_calls": 0,
  "search_tool_calls": 0,
  "ratio": "all_intuition" | "all_search" | "mixed" | "no_data"
}
```

This data already exists in scattered form (codebase_model entity reads,
artifact creates, transaction narrative). The substrate change is
**aggregating it per-goal as a first-class calibration signal**.

### 3. Routing rule in the gate

CHECK gate (and PREFLIGHT auto-proceed gate) gain a new check:

```
if vectors.know >= 0.75 AND ratio == "all_intuition" AND goal_type in
   {greenfield, refactor_of_unread_code, spec_implementation, ...}:
   → route to "investigate" with explicit reason:
     "Vectors claim high know but evidence is all-intuition. For
      this goal type, external grounding is required before
      proceeding. Suggested noetic actions: read X, grep Y."
```

Goal-type detection is the hardest piece. v0 could be:
- Heuristic from goal objective text (keyword scan: "refactor",
  "implement", "wire", "extract" → likely needs search; "polish",
  "docs", "rename" → may be intuition-OK)
- Plus per-AI calibration history: if claude-opus-4-7 has historically
  shipped intuition-only at high vectors AND those POSTFLIGHTs had
  high grounded-divergence, raise the bar
- v1 could expose `goal.work_mode` field (`investigation` |
  `iteration` | `polish` | `greenfield` | `spec_impl`) that the
  AI declares at goal-create time

### 4. (Stretch) Surface the ratio in PREFLIGHT response

Today PREFLIGHT returns `noetic_guidance` to the AI. Add an
`epistemic_provenance` block to the same response showing the ratio
**from the prior session/transactions on this goal**, so the AI sees
its own pattern. This is the AI-facing analog of what the chat surfaces
to the human via the Phase 14 badge.

## What the chat side will do (Phase 14b, goal 75c7f029)

In parallel, the ecodex/chat side will:
- Tag explicit artifact-create slash commands (`/finding`, `/decision`,
  `/unknown`, `/batch`, etc.) with source='search' on the artifact
  itself when calling the empirica CLI
- Tag turns from translator event-tap subscribers (Phase 3, 15b) as
  'search' when the upstream model emitted tool-call events
- Pass intuition/search ratio (from Turn metadata) into PREFLIGHT
  payload when chat-mode is the surface

This gives the Sentinel a real data feed once the substrate primitive
lands. Until then it's chat-only data-for-the-human.

## Suggested first-step scope for empirica-core

If you want to ship the smallest useful slice first:

1. **Add `epistemic_source` field to artifact tables** — backward-compat
   default `None`, accept value on log-* CLI commands and via batch
   `log-artifacts -` payloads.
2. **Surface aggregate in `calibration_reflection`** — count source-
   tagged artifacts per goal, add ratio to the existing
   `assessment_notes` block. No routing change yet — just visibility.
3. **Document the rule** in `/empirica-constitution` and
   `~/.claude/empirica-system-prompt.md` so AIs (CC, chat, future)
   understand the new field exists and how to use it honestly.

That's a minimal v0 — proves the primitive without changing routing.
The routing rule can come once the data shape is settled and there's
empirical history to calibrate the gate threshold against.

## Caveat

I am the AI that demonstrated this exact failure mode. The fix above
is what I would have wanted to be measured against — but you should
weight that accordingly. David's framing is the load-bearing piece;
mine is implementation suggestion.

---

*Generated by ecodex/empirica-chat session 99feea74-7fe5-479e-8003-9477fc2dcb63
on 2026-05-03 as a hand-off artifact. Mistake artifact:
534c920c-0bdd-4cd4-811d-d4c18591c881 in empirica project.*
