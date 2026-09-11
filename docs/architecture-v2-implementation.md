# Architecture V2 — Implementation Report

**Date:** 2026-09-11  
**Scope:** P0 + P1 from [architecture-audit.md](architecture-audit.md) / [architecture-v2.md](architecture-v2.md)  
**Constraint:** no new LLM agent roles; no K8s/vector DB/distributed infra.

---

## 1. What the audit found (summary)

Critical gaps in MVP: theatrical verification (LLM status without recompute), mock PASS-by-call-count, final report from Chief prose, Cursor FS escape via project cwd, no evidence graph / run manifest / budget, shared full claim dump to reviewers, sequential V→RT coupling.

---

## 2. What was fixed (P0)

| ID | Fix |
|----|-----|
| P0.1 | Docs now describe `STAGE_ROLES` as stage table, not dependency graph |
| P0.2 | `SynthesisBundle` + gated `render_final_report`; LLM polish cannot redefine proof |
| P0.3 | DecisionLog canonical owner = `LabRuntime` only; chief does not append |
| P0.4 | `CursorSDKProvider(reasoning_only=True)` uses empty temp cwd; project cwd ignored |
| P0.5 | `MathCheck` + planted-error tests; verification forced FAIL on critical checks |
| P0.6 | Mock PASS only via fixture (`deterministic_all_passed` / override), never call count |

---

## 3. What was fixed (P1)

| Item | Implementation |
|------|----------------|
| RunManifest | `memory/run_store.py` → `.runs/<run_id>/manifest.json` |
| Immutable computations | `ComputationArtifact` per id; overwrite raises `FileExistsError` |
| RunBudget | `orchestrator/budget.py`; `BUDGET_EXCEEDED` terminal state |
| ReviewBundle | Blind claims (no confidence/agent_id); shared frozen path for V∥RT |
| Deterministic checks | `checks/math_check.py` + runner before review |
| Independent V∥RT | `LabRuntime._run_independent_review` via `asyncio.gather` |
| Adjudication | Deterministic FAIL cannot become PASS |
| Evidence graph | JSON nodes/edges (`memory/evidence_graph.py`) |
| CONSENSUS vs INDEPENDENT_EVIDENCE | `AgreementType` on checks/verification/adjudication |
| Claim versioning | `supersede_claim`, `version`, `supersedes`/`superseded_by` |
| IterationPolicy | Reason → RESEARCH / ANALYSIS / CALCULATION / SIMULATION |
| HITL | `AgentResult.hitl_request` → `AWAITING_HUMAN`; CLI `--resume` |
| Tool taint | `trust_level` + `data_not_instructions` on registry results |
| Source trust | `SourceTrustTier`; `mock://` cannot be FACT |

---

## 4. Pipeline after P0/P1

```
USER → Chief → specialists → tools → artifacts / evidence graph
  → DeterministicChecks → ReviewBundle
  → Verification ∥ Red Team → Adjudication
  → FAIL/DISPUTED/INSUFFICIENT → IterationPolicy
  → PASS → SynthesisBundle → final_report.md (gated)
```

---

## 5. Tests

Full suite: **`pytest` → 33 passed** (was 15).

New coverage includes: planted calc error, mock no PASS-by-count, synthesis gates, DecisionLog single-owner, manifest, immutable artifact, budget stop, blind bundle, V/RT isolation, adjudication hard gate, claim supersede, tool UNTRUSTED, Cursor reasoning-only cwd, resume run_id.

---

## 6. Remaining limitations (explicit)

1. **Adaptive planner** — V2.3 validates a TaskGraph; fully autonomous graph revision beyond IterationPolicy→vN is not built. Chief `follow_up_tasks` remain advisory.  
2. **Evidence graph** is JSON file, not a query engine; links are minimal.  
3. **Cursor SDK** isolation depends on SDK honoring temp `cwd` + prompt; not a formal capability sandbox.  
4. **Multi-model anti-collusion**: V2.4a `RoutingPolicy` can require distinct models; default CI config still uses one mock provider. Different models ≠ `INDEPENDENT_EVIDENCE`.  
5. **Research** has a real `ResearchProvider` + `SearchProvider` pipeline (mock/replay/web). `mock://` remains STUB. See [research-pipeline.md](research-pipeline.md).  
6. **HITL resume** restores state/run_id; interactive stdin decision UI not built (auto-approve / pending payload only).  
7. **Record/replay** LLM fixtures: `ReplayProvider` plus routing metadata (V2.4a). Mock remains the CI default.  
8. **Pint + DeterministicVerifier** implemented for `VerificationSpec` (see [engineering-verification.md](engineering-verification.md)). Legacy `MathCheckRequest.required_units` is still string-tag equality.  
9. Re-running a COMPLETED project starts a **fresh** UNDERSTANDING run (by design); old claims remain on disk (may accumulate across runs).

---

## 7. Most logical P2 next

1. Run-scoped claim namespaces / purge-or-freeze project claims per run  
2. Real research backend with resolvable PRIMARY/SECONDARY sources — **done (V2.2 research pipeline)**; see [research-pipeline.md](research-pipeline.md)  
3. Provider router + cost tracking from real usage — **done (V2.4a routing + observability metrics)**; economics optimizer not built. See [multi-model-routing.md](multi-model-routing.md).  
4. Docker/cgroup sandbox limits — **done for local subprocess (V2.4b) and Docker backend (V2.4c)**. See [compute-sandbox.md](compute-sandbox.md), [docker-sandbox.md](docker-sandbox.md).  
5. Richer evidence-graph queries for final decision paths  
6. Optional second model mandatory for verification vs author — **policy exists** (`independence.require_different_model_from_author`); default off for mock CI.  

**Do not** add more agents until tools + checks + research backends need them.

---

## 8. V2.1 Knowledge system (follow-up)

Implemented run-scoped claim namespaces, ApprovedKnowledge promotion/demotion, Conflict model, Evidence Graph integrity + query API, run comparison, freeze/immutability checks, ResearchProvider interface, record/replay foundation, and non-destructive migration.

Docs: [knowledge-architecture.md](knowledge-architecture.md), [knowledge-migration.md](knowledge-migration.md).

Tests: `tests/knowledge/`, `tests/replay/` — full suite **55 passed**.

---

## 9. V2.2 Engineering verification

**Goal:** deterministic verification of quantitative engineering claims. Pint lives only in this layer.

```text
Claim
 ↓
VerificationSpec
 ↓
DeterministicVerifier
 ├── Units
 ├── Computation
 ├── Tolerance
 ├── Bounds
 └── Sanity
 ↓
VerificationResult
 ↓
VerificationAgent
 ↓
Adjudication
```

| Piece | Implementation |
|-------|----------------|
| Pint | `checks/units.py` — parse / convert / dimensionality via `UnitRegistry` |
| Safe eval | `checks/safe_eval.py` — recursive AST walker; no `eval`/`exec`/imports |
| Spec / result | `VerificationSpec`, `VerificationResult`, `CheckStatus` (not a second Claim schema) |
| Verifier | `checks/verifier.py` — `DeterministicVerifier` |
| Legacy | `MathCheckRequest` expressions adapt into dimensionless `VerificationSpec` |
| Agent gate | `VerificationAgent` copies CheckStatus into `recomputed.deterministic`; cannot promote FAIL / `INCOMPATIBLE_DIMENSIONS` to PASS |
| Graph | Existing `GraphNodeType.CHECK` + `TESTS` / `VERIFIED_BY` (CHECK now allowed on `VERIFIED_BY`) |
| Limits | `config.verification.limits` (research-limits analogue, not a second RunBudget) |

Docs: [engineering-verification.md](engineering-verification.md). Tests: `tests/test_engineering_verification.py`. Full suite **105 passed**.

**Not in this slice:** Docker sandbox, RAG, physics simulation engine, LLM-authored specs as the sole source of expected values (claims still carry the spec, like `math_check`).

---

## 10. V2.3 Data-driven TaskGraph Planner

**Goal:** LLM proposes a plan; a deterministic validator is the only authority for execution.

```text
Problem → Planner → TaskGraphProposal → validate_task_graph → TaskGraph → LabRuntime
```

| Piece | Implementation |
|-------|----------------|
| `TaskSpec` | Evolved in `core/models.py` (single type) |
| `TaskGraph` / proposal | Serializable DAG + untrusted proposal |
| Validator | `planner/validator.py` — DAG, roles, schemas, inputs, budget, V∥RT independence |
| StaticPlanner | Default pipeline as data (`runtime.planner: static`) |
| LLMPlanner | Structured proposal only; no tools/files/budget |
| Runtime | Ready-set execution of the approved graph |
| Artifacts | `.runs/<run_id>/planner/` + `RunManifest.task_graph_hash` |
| CLI | `python -m ai_lab plan <project>` |

Docs: [taskgraph-planner.md](taskgraph-planner.md). `IterationPolicy` is unchanged; graph versioning is the extension point (`supersedes` / `reason`).

**Not in this slice:** adaptive autonomous planner, conditional tasks, Docker, RAG, UI.

---

## 11. V2.4a Multi-model router

**Goal:** several LLM configurations with a deterministic routing policy, without a second LLM stack.

```text
TaskGraph → RoutingPolicy → validate_routing_policy → LLMRouter → LLMProvider
```

| Piece | Implementation |
|-------|----------------|
| `ModelConfig` | `llm/config.py` — serializable identity + hash |
| `PolicyLLMRouter` | Selects config, delegates to existing `LLMProvider` |
| Registry | `llm/registry.py` — `provider id → instance` (no if/elif in the router) |
| Independence | `classify_independence` → `FULL` / `PARTIAL` / `NONE` / `INVALID` |
| Manifest | `RunManifest.routing_policy_version` + `model_routing` |
| CLI | `python -m ai_lab routing` |

Hard gates, V∥RT isolation, `AgreementType`, HITL, IterationPolicy, and `RunBudget` are unchanged. Different models are **not** automatically `INDEPENDENT_EVIDENCE`.

Docs: [multi-model-routing.md](multi-model-routing.md). Tests: `tests/test_multi_model_routing.py`. Full suite **162 passed**.

---

## 12. V2.4b Reproducible compute sandbox

**Goal:** move general-purpose Python out of ad-hoc in-process/tempdir execution into a killable subprocess sandbox, without replacing `DeterministicVerifier`.

```text
TaskGraph → ToolRegistry → python.execute → ComputeSpec → LocalSubprocessSandbox → ComputationArtifact
```

| Piece | Implementation |
|-------|----------------|
| `ComputeSpec` | `sandbox/models.py` — extra keys forbidden; policy ceiling is trusted config |
| `LocalSubprocessSandbox` | spawn `python -I runner.py`; Job Objects on Windows when available |
| `DockerSandbox` | extension point; `execute()` raises (not a dependency) |
| Artifact | existing `ComputationArtifact` + `computation_hash` |
| CLI | `python -m ai_lab sandbox` |

Not a fully secure container. Network deny on the local backend is **UNSUPPORTED** (policy requested). Filesystem jail is **BEST_EFFORT**. Timeout + process kill + env allowlist + output caps are **HARD**.

Docs: [compute-sandbox.md](compute-sandbox.md). Tests: `tests/test_compute_sandbox.py`. Full suite **189 passed**.

**Not in this slice:** Docker runtime (added in V2.4c), RAG, CFD/FEA, GPU, K8s.

---

## 13. V2.4c Docker hard-isolation backend

**Goal:** second `ComputeSandbox` backend with real container isolation, without a second execution architecture and without silent fallback.

```text
TaskGraph → ToolRegistry → python.execute → ComputeSpec → DockerSandbox → ComputationArtifact
```

| Piece | Implementation |
|-------|----------------|
| `DockerSandbox` | `sandbox/docker.py` — `docker run` argv, `shell=False` |
| `build_docker_command` | `sandbox/docker_cmd.py` — pure, testable, no user flags |
| Image | trusted `sandbox.docker.image` / optional digest; `--pull=never` |
| Isolation | `--network=none`, `--read-only`, `--cap-drop ALL`, `--memory`, `--cpus`, `--pids-limit` |
| Discovery | `detect_docker_capabilities()` — binary ≠ daemon ≠ backend |
| CLI | `python -m ai_lab sandbox` prints both backends |

Missing Docker → `SandboxUnsupportedError`. `fallback_backend: none`. Mutable tags → `PARTIAL_ENVIRONMENT` unless digest is known.

Docs: [docker-sandbox.md](docker-sandbox.md). Tests: `tests/test_docker_command_builder.py`, `tests/test_docker_sandbox.py` (`@pytest.mark.docker` skipped without engine).

**Not in this slice:** Kubernetes, GPU, Swarm, remote daemon, automatic image build, arbitrary pip from LLM.

---

## 14. V2.5 Engineering Simulation Framework + local UI

**Goal:** `SimulationSpec` as the scientific object; first solver `UniaxialTensionSolver`; simple local UI over `LabRuntime`.

Docs: [engineering-simulation.md](engineering-simulation.md), [ui.md](ui.md). Tests: `tests/test_engineering_simulation.py`, `tests/test_ui_api.py`.

Invariant unchanged: LLM proposes → deterministic validate → solver computes → sandbox isolates untrusted Python → verifier recomputes → Red Team attacks → adjudicator adjudicates → Evidence Graph remembers.


