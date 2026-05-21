# Internal Classes Reference

A flat index of internal Empirica classes — their role in the system,
their file location, and the public-ish surface they expose. This is
intentionally a reference inventory, not API documentation. Use this to
locate the class; read its docstring for the contract.

If a class is missing from this index, that's usually a sign it should
either be properly documented or moved to a less prominent home.

## Listener + heartbeat subsystem

- `HeartbeatEmitter` — `empirica/core/loop_scheduler/heartbeat.py`. Daemon
  thread inside `empirica loop listen` that posts liveness signals to
  Cortex's `/v1/listeners/heartbeat` every 45s. Per prop_5rlp6tk (option-b).
- `ListenerStatus` — `empirica/core/loop_scheduler/persistent_listener.py`.
  Snapshot dataclass: `(ai_id, backend, installed, active, unit_path, log_path)`.
- `PersistentListenerService` — same file. Install / uninstall / inspect the
  persistent listener service for an ai_id. systemd-user on Linux/WSL2,
  launchd on macOS.
- `ListenerServiceUnavailable` — sentinel exception when no supported backend exists on the host.
- `ListenerStopped` — `empirica/core/loop_scheduler/listener.py`. Signal-handler
  exception used to unwind the held-curl loop cleanly on SIGTERM.
- `ListenerEntry` — `empirica/core/cockpit/listener_registry.py`. Declarative
  registry entry per registered in-session listener (name/topic/state).
- `ListenerRegistry` — same file. The atomic-write registry that owns
  `listeners_<instance>.json` + pause sidecars.
- `ListenerInstallRequest` / `ListenerUninstallRequest` —
  `empirica/core/cockpit/listener_install_request.py` and `_uninstall_request.py`.
  Cockpit→Claude pickup payloads.
- `LoopRegistry`, `LoopEntry`, `LoopStatus`, `LoopUnitFiles`,
  `LoopInstallRequest`, `LoopUninstallRequest`,
  `LoopSchedulerUnavailable`, `SystemdLoopScheduler`, `SystemdUnavailable`,
  `LaunchdLoopScheduler`, `LaunchdUnavailable`, `BackoffState`,
  `SchedulingState`, `SchedulePlan` — all in `empirica/core/cockpit/loop_registry.py`
  + `empirica/core/loop_scheduler/{systemd,launchd}.py`. Timer-driven
  loop scheduling and registry primitives.

## Cockpit + instance state

- `CockpitStateSnapshot`, `InstanceInfo`, `StatusWindow`, `OpenGoal`,
  `RecentAction`, `SentinelPauseStatus`, `StatuslineSummary`,
  `StatuslineCache`, `StatuslineCacheEntry`, `LivenessResult`,
  `KillResult`, `LaunchResult`, `LauncherConfig`, `PaneSpec`,
  `GroupSpec`, `GroupLaunchResult`, `GroupsLaunchResult`, `StopResult`,
  `WakeResult` — assorted dataclasses in `empirica/core/cockpit/` describing
  the instance/loop/listener state surface the TUI renders.
- `SystemStatus`, `SystemDashboard` — `empirica/core/system_dashboard.py`.
  Aggregated host snapshot for the dashboard view.

## Compliance + calibration

- `ComplianceStatus`, `ComplianceResult` — domain compliance check
  outcomes (`empirica/core/compliance/`).
- `EpistemicRollupGate`, `RollupResult`, `EpistemicAssessmentSchema`,
  `BrierDecomposition`, `CalibrationTrend`, `GroundedBelief`,
  `GroundedVectorEstimate`, `GroundedCalibrationManager` — calibration
  pipeline internals in `empirica/core/calibration/` and `epistemic/`.
- `EvidenceProfile`, `EvidenceQuality`, `EvidenceType` — evidence taxonomy
  used by the grounded calibration system.
- `RegulationDecision` — outcome dataclass for regulation-mapping checks.

## Artifact extraction + transcript parsing

- `ArtifactExtractor`, `ExtractionResult` — extract findings/decisions/
  dead-ends/mistakes/unknowns from text (`empirica/core/extraction/`).
- `ExtractedFinding`, `ExtractedDecision`, `ExtractedDeadEnd`,
  `ExtractedMistake`, `ExtractedUnknown` — typed records produced by the
  extractor.
- `TranscriptParser`, `TranscriptRecord`, `ContentBlock`, `ContentBlockType`,
  `ContentType`, `ConversationTurn`, `TurnKind` — transcript ingestion
  for Claude.ai exports and Claude Code session jsonl
  (`empirica/core/transcripts/`).

## Bus + observers

- `BusStatus`, `SqliteBusObserver`, `QdrantBusObserver`, `ProviderError`,
  `ProviderRegistry`, `TranslatorError` — dispatch bus internals
  (`empirica/core/dispatch_bus*.py`).

## Noetic batch

- `NoeticBatchInput`, `NoeticBatchResult`, `ReadOperation`, `ReadResult`,
  `GrepOperation`, `GrepMatch`, `GrepResult`, `GlobOperation`, `GlobResult`,
  `InvestigateOperation`, `InvestigateResult` — schema dataclasses for
  `empirica noetic-batch` (`empirica/core/noetic_batch/`).

## Identity + injection

- `NodeIdentity`, `IntegrityStatus`, `InjectionChannel`, `InjectionRequest` —
  identity propagation + sentinel-gate context injection
  (`empirica/core/identity/`, `empirica/core/injection/`).
- `EvictionResult`, `ForgetResult` — memory eviction outcomes
  (`empirica/core/memory/`).

## Sources, decisions, assumptions

- `GitAssumptionStore`, `GitDecisionStore`, `GitSourceStore` —
  git-notes-backed artifact stores
  (`empirica/core/canonical/empirica_git/`).
- `ScoredFinding`, `WorkflowPattern`, `WorkflowSuggestion`,
  `EcosystemGraph`, `TrajectoryTracker`, `TrajectoryPoint`,
  `TransactionOutcome`, `BatchBudgets`, `BatchSummary`,
  `BudgetThresholds`, `BenchmarkResult`, `RelationshipType`,
  `RecordType`, `FactStatus`, `MemoryStatus`, `MemoryZone`,
  `ContextItem`, `EntityType`, `ConstraintType`, `DomainAllocation`,
  `AssessmentResult` — supporting types across the workflow engine.

## Network + scanning

- `KEVFeed`, `WebEvidenceCollector`, `_HTMLStructureValidator`,
  `ScanRule` — service-audit and semantic scan internals
  (`empirica/core/scan/`, `empirica/core/docs/`).
- `OrchestrationPlan`, `ProfileImporter`, `ProjectSpec`,
  `SessionIndex`, `SessionMetadata`, `SlashCmd`,
  `ToolChain`, `NotificationItem`, `NotificationSummary`,
  `ActionError`, `ConfigStatus`, `GateStatus` — orchestration +
  profile import surface.

---

This index is maintained alongside the codebase; classes added or
removed should round-trip through here so the reference stays
load-bearing rather than ornamental.
