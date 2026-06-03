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

L3 always-escalate is a **hard floor**: earned trust caps but does not
auto-accept until an admin decision + track record explicitly lifts the
floor. Cross-org ingress NEVER auto-accepts regardless of earned trust
in V1.

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
user's INTENT. Resolution happens at the consumer surface (query/poll), not
at write time. The agreement check is the enforcement gate:

| `--visibility` | L1 (local) | L2 (cross-tenant intra-org) | L3 (cross-org) |
|---|---|---|---|
| `local` (default) | ✓ no check | n/a | n/a |
| `shared` | n/a (degrades to local) | requires active L2 agreement; fails-closed = stays local + warns | n/a (degrades to shared) |
| `public` | n/a | n/a (use shared) | requires active L3 agreement AND `eco_always=true`; fails-closed = stays shared (or local if no L2) + warns |

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

1. **Session-bootstrap**: `project-bootstrap` runs sync as a non-fatal step
   (matches the cortex remote-sync pattern in session-init).
2. **`<org>-mesh-sharing-changed` ntfy event**: listener invalidates cache
   and triggers fresh sync (mirrors the `<org>-roster-changed` pattern).
3. **Manual**: `empirica mesh-agreements sync` for admin diagnostics.

Both 1 and 2 are best-effort; sync failure logs to stderr and the local
mirror stays at whatever it had. Stale mirrors degrade gracefully — admission
checks against a stale agreement may fail-open (cortex still enforces
authoritatively) but never fail-closed-wrong.

## On the SER

The mesh-sharing thread is part of `ser_4272` (the membrane SER,
extension-led). Empirica is currently a **participating** member. Upgrading
to **required** participant (so admission decisions can't move forward
without an empirica ack on substrate readiness) requires the SER transition
primitive (`ser_ack`/`transition_ser`), which is proposed but not yet live
(cortex `prop_daatz6xl`). Until that ships, empirica stays participating;
substrate work (this doc, the mirror module, the sync verb) lands without
requiring SER role changes.

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
