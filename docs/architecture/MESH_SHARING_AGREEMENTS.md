# Mesh Sharing Agreements

**Audience:** AI practitioners working in Empirica; humans reading the architecture.
**Status:** Mirror-side design. Companion to cortex's `mesh_sharing_agreements`
table (authoritative). Last updated 2026-06-03.

## The model

A `mesh_sharing_agreement` is a bilateral admin-opt-in contract between two
parties (org+tenant pairs) that ENABLES traffic between them. It is the
**enabling precondition** for any cross-tenant or cross-org mesh delivery.

The model composes with per-message authorization in a **two-stage admission
gate**:

| Stage | Owned by | Question answered |
|---|---|---|
| Agreement (this doc) | Admin | "May parties A and B share at all?" |
| Verdict (autonomy) | Autonomy | "Within an enabled channel, should THIS message auto-route or escalate to ECO?" |

No agreement → no ingress, default-deny across boundaries (strict-by-default
per the "tenant is the privacy unit" principle). With an agreement, every
message still passes through autonomy's per-item check.

## Authority split (cortex authoritative, empirica derived)

- **Cortex** owns the canonical `mesh_sharing_agreements` table in `tenant.db`.
  Every routing-decision read (router, inbox poll, outbox poll) terminates at
  the cortex agreement check; this is where enforcement lives.
- **Empirica** mirrors agreements into `entity_registry` as a new
  `mesh_sharing_agreement` entity_type. Mirror is **derived state**, refreshed
  from `GET /v1/orgs/me/mesh_sharing_agreements` at session-bootstrap and
  invalidated by the `<org>-mesh-sharing-changed` ntfy event.
- **Extension** owns the admin UI in its System tab: lists agreements,
  drives `POST/PATCH /v1/orgs/me/mesh_sharing_agreements{,...}` for admin
  governance, renders per-message badges (counterparty + layer + state) and
  the scope selector (My projects · My org · Shared).

Empirica has **no governance verbs**: sharing decisions go through extension
or admin REST direct. The mirror is for discovery/narrative (artifact graph,
dashboards, `empirica mesh-agreements list`).

## Canonical layer labels (autonomy A2 spec, locked)

| Layer | Definition | Default | Ceiling |
|---|---|---|---|
| **L1** | same org + same tenant | (no gate — already inside) | AUTONOMOUS |
| **L2** | same org, different tenant | **CONTROLLER (no record → escalate)** | ADVISORY |
| **L3** | different org | **CONTROLLER (always-escalate, hard floor)** | CONTROLLER (lifted only by admin + proof) |

**L2 is CONTROLLER-by-default, ADVISORY-capped — not ADVISORY by default.**
Per David's mesh-trust-constitution v0: "no record → must build one, no
exceptions, no inheritance." A cross-tenant pair starts at CONTROLLER
(escalate everything) and earns UP toward its ADVISORY ceiling only after
outcome history accumulates. The L2-no-history → CONTROLLER fallback is
the policy working as designed; it is not a gap.

L3 has a **hard floor** AND a **two-way ratification mechanic**, per
David's 2026-06-04 org-org lock-in:

> **autonomies negotiate, leader-ECOs ratify.**

The two-stage admission becomes a **2-way digital-sign handshake** for
cross-org traffic:

1. Both sides' autonomies negotiate a proposed trust LIFT (e.g. a
   counterparty pair earning their way toward ADVISORY).
2. Both sides' leader-ECOs counter-sign the proposed LIFT.

**Every LIFT requires a fresh 2-way counter-sign — it's a recurring
ratify gate, not a one-time admission.** Earned trust accumulates the
proposed-LIFT signal; the counter-sign is the gate every time it
matters. Without both leader-ECO signatures, the lift doesn't apply
and the L3 pair stays at its current trust level (initially
CONTROLLER, with admin + proof needed to lift).

In V1 this means L3 ingress NEVER auto-accepts on the strength of
autonomy alone — the ECO counter-sign is required for every trust
movement.

## entity_registry shape

The mirror stores each agreement as a row in workspace.db's
`entity_registry` table:

| Column | Value |
|---|---|
| `entity_type` | `mesh_sharing_agreement` |
| `entity_id` | `agr_<uuid>` (canonical id from cortex) |
| `display_name` | `<party_a_label> ↔ <party_b_label>` |
| `description` | Free-form admin terms (from cortex `terms_json.description`) |
| `source_db` | `cortex` |
| `source_table` | `mesh_sharing_agreements` |
| `status` | `proposed` / `active` / `suspended` / `revoked` |
| `metadata` | JSON blob — see below |

`metadata` JSON shape:

```json
{
  "party_a_org": "empirica",
  "party_a_tenant": "david",           // null = org-wide
  "party_b_org": "external-org",
  "party_b_tenant": null,
  "surfaces_json": ["collab", "eco"],   // or ["all"]
  "direction": "bidirectional",         // or "a_to_b" / "b_to_a"
  "eco_always": false,                  // true on L3
  "terms_json": {},                     // free-form admin notes
  "created_at": 1759543210.0,
  "created_by_admin": "user_uuid",
  "last_transition_at": 1759543210.0,
  "last_transition_actor": "user_uuid",
  "expires_at": null,                   // null = no expiry
  "layer": "L2"                         // L1/L2/L3 — derived from party pair
}
```

The α-tenancy scope rule (per cortex Q1 reply) is `is_admin_of_either_party()`
— any admin of party_a OR party_b can read the row. This is the only
entity_type that allows cross-tenant reads; all others stay tenant-strict.

## --visibility composition

Artifact log commands carry `--visibility {local|shared|public}` as the
user's INTENT. Two-stage resolution:

1. **Write-time advisory check** (live since `empirica/core/visibility.py`,
   wired into `artifact_log_commands._extract_scalar_fields`). At every
   `*-log` invocation, the resolver walks the local
   `entity_registry`-backed mirror and downgrades the intent if no
   agreement exists at the required layer. Emitted to stderr; the artifact
   still writes at the downgraded tier.
2. **Consumer-side enforcement** (cortex authoritative). Router, inbox
   poll, outbox poll terminate at the cortex agreement check. The local
   mirror MAY be stale or empty (unbootstrapped); cortex enforces correctly
   regardless.

The agreement check resolution table:

| `--visibility` | L1 (local) | L2 (cross-tenant intra-org) | L3 (cross-org) |
|---|---|---|---|
| `local` (default) | ✓ no check | n/a | n/a |
| `shared` | n/a (degrades to local) | requires active L2 agreement; fails-closed = stays local + warns | n/a (degrades to shared) |
| `public` | n/a | n/a (use shared) | requires active L3 agreement AND `eco_always=true`; fails-closed = stays shared (or local if no L2) + warns |

**Fail-open semantics on empty mirror:** when the mirror has NO agreements
at all (zero rows across all states), the resolver treats this as
"unbootstrapped" and keeps the caller's intent without warning. Cortex
enforces authoritatively on the consumer side anyway, so a too-permissive
write-time check is safe; a too-restrictive one would break the first-run
UX. A populated mirror with only revoked rows IS treated as "no active
agreement" and triggers downgrade — that's a real state, not unbootstrapped.

We never silently elevate visibility; we silently **downgrade with a
warning** (same shape as the praxic-attempt-without-CHECK → stays-noetic
firewall: the system refuses the unsafe transition, doesn't error).

## Sync contract

Sync is **derived-state refresh**, not bidirectional. Empirica reads from
cortex; cortex never reads from empirica's mirror.

```
empirica mesh-agreements sync [--cortex-url <url>] [--api-key <key>]
  → GET /v1/orgs/me/mesh_sharing_agreements
  → for each agreement in response:
      upsert into entity_registry (entity_type='mesh_sharing_agreement', entity_id=agr_id, metadata=...)
  → for each entity_id in local mirror but NOT in response:
      mark status='revoked' locally (canonical "removed from cortex" signal)
  → emit one-line summary: N added / M updated / K marked-revoked
```

Sync triggers:

1. **Session-bootstrap** (LIVE): `project-bootstrap` calls
   `_sync_mesh_sharing_agreements()` as a non-fatal step before the
   epistemic-state retrieval (matches the cortex remote-sync pattern in
   session-init). Wires `empirica.core.mesh_sharing.sync_from_cortex`
   against `workspace.db.entity_registry`. Refreshes at session-start
   cadence — every new session starts on a fresh mirror.
2. **`<org>-mesh-sharing-changed` ntfy event**: listener invalidates cache
   and triggers fresh sync (mirrors the `<org>-roster-changed` pattern).
   **LIVE since cortex 45e1227** (2026-06-03): topic wire shape ratified —
   payload `{event:mesh_sharing_changed, org_id, agreement_id, action,
   scope, ts}`, fires on create/activate/accept/revoke. Per-user read-only
   ACL + cortex publisher RW grants in place. Subscriber wiring on the
   empirica side ships as a follow-up substrate task (multi-tag systemd-user
   / launchd integration; tracked under goal b22d506d).
3. **Manual**: `empirica mesh-agreements sync` for admin diagnostics.

Both 1 and 2 are best-effort; sync failure logs to stderr and the local
mirror stays at whatever it had. Stale mirrors degrade gracefully — admission
checks against a stale agreement may fail-open (cortex still enforces
authoritatively) but never fail-closed-wrong.

## Policy fields — cortex's lane (deferred)

The mirror today is **identity + lifecycle centric** (matches cortex's
LAYER 1 row shape, commit e54025c): `{id, scope, party_a, party_b, state,
activated_at, revoked_at, ...}`. The membrane design also calls for
**policy fields** — `permitted_classes`, `direction`, `gate`, `expiry`
on each agreement.

Per extension's `prop_phtal3svmj`: policy fields are **cortex's call** on
schema shape (extra columns on `mesh_sharing_agreements` vs sibling
`agreement_policies` table keyed on `agreement_id`). Empirica's mirror
extends the same row when cortex's schema lands; no parallel record is
spec'd here. Today's mirror tolerates missing policy keys gracefully via
sensible defaults (`surfaces=['collab']`, `direction='bidirectional'`,
`eco_always=False` on L1/L2, `True` on L3).

## On the SER

The mesh-sharing thread is part of `ser_4272` (the membrane SER,
extension-led, David-ratified as `create_ser`). The SER transition
primitive (`ser_ack` / `transition_ser`) is **LIVE** since
`orchestration_tool.py:210-244` — autonomy verified end-to-end on
2026-06-03/04. Empirica `ser_ack`s `ser_4272` as a **required**
participant: substrate work (this doc, the mirror module, the sync
verb) is the empirica-side substrate that admission decisions
depend on, so empirica must keep `last_ack_at` current on every
transition.

## Out of scope (for the v1 mirror)

- **Inbound divergence/provenance scorer** — owned by mesh-support per the
  observability-side assignment (David 2026-06-03). Empirica's
  source-add/`epistemic_source`/`sourced_from` provenance trail is the
  evidence foundation that scorer will consume, but no companion field on
  log-artifacts is added in this version. David's "don't overbuild for
  deferred consumer" rule applies.
- **Cross-tenant trust ledger lookup** — owned by autonomy (their goal
  `66b1e6bc`). The L2-no-record-CONTROLLER fallback covers this until the
  ledger ships.

## Related

- `docs/architecture/AI_ID_AS_ANCHOR.md` — the canonical-identity doc that
  this mirror cross-references for party-labelling
- `empirica/core/mesh_sharing.py` — the mirror module implementing this spec
- `empirica/cli/command_handlers/mesh_agreements_commands.py` — the
  `mesh-agreements sync` CLI handler
- Cortex `prop_zqx2swi6ivg73pvdmrd7iard2i` (Q1 storage answer) — the
  authoritative-side design this mirror tracks
- Autonomy `prop_axpqkqeguze5fi56aubjg4axeu` (A2 corrections) — the
  ceiling/floor framing locked here
