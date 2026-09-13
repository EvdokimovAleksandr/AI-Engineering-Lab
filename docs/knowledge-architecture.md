# Knowledge Architecture V2.1

Scientific knowledge system for AI Engineering Lab: run-scoped claims, evidence graph, approved knowledge, conflicts, and provenance queries.

See also: [knowledge-migration.md](knowledge-migration.md), [architecture-v2-implementation.md](architecture-v2-implementation.md).

## Data model

```text
Project
 ├── Runs (.runs/<run_id>/)
 │    ├── manifest.json (+ freeze_seal)
 │    ├── claims/<claim_id>_vN.json
 │    ├── computations/
 │    └── reviews/
 ├── Knowledge/
 │    ├── approved/
 │    ├── conflicts/
 │    ├── conclusions/
 │    ├── claims_global_index.json
 │    └── graph/{nodes,edges}.json
 ├── Decisions (decision_log.jsonl + decisions_trace/)
 └── Conclusions
```

### Canonical claim identity

```text
{project_id}/{run_id}/{claim_id}/v{version}
```

Not a filename. Stored as `Claim.canonical_id`.

### Claim lifecycle

`ACTIVE` → `SUPERSEDED` | `REJECTED` | `DISPUTED` | `ARCHIVED` | `DEMOTED`

History is never deleted; use `supersede_claim` / `REFUTES` edges.

### Visibility modes

| Mode | Who (default) | Contents |
|------|----------------|----------|
| `CURRENT_RUN` | Verification, Red Team, Theorist, Simulation | Only this run's claims |
| `PROJECT_HISTORY` | Research | All runs (explicit) |
| `APPROVED_KNOWLEDGE` | Chief (+ merged with CURRENT_RUN) | Promoted entries only |

Nobody gets “load everything” by default.

### Claim support status + evidence lineage (PR-05)

Epistemic gate on `Claim.support_status` (not a second store):

| `ClaimSupportStatus` | Meaning for synthesis |
|----------------------|------------------------|
| `PROPOSED` | Default; not proven unless verified computation + lineage |
| `SUPPORTED` / `WEAKLY_SUPPORTED` | May enter grounded synthesis; **requires** `evidence_ids` / `calculation_ids` |
| `UNVERIFIED` / `CONTRADICTED` / `REJECTED` | Never accepted quantitative truth |

Mapping (no duplicate truth):

- `EvidenceKind` — shape of the statement (`FACT`, `CALCULATION`, …)
- `ClaimLifecycle` — store history (`ACTIVE`, `SUPERSEDED`, …)
- `ClaimSupportStatus` — synthesis / coverage eligibility
- `EvidenceType` on `EvidenceRecord` — provenance class:
  `LITERATURE` | `EXPERIMENTAL` | `CALCULATED` | `SIMULATED` | `ASSUMED` | `USER_PROVIDED`

Rules: `CALCULATED`/`SIMULATED` require `computation_artifact_id`; `LITERATURE` requires `source_id`.
Claim → evidence attach across investigations raises `CONTEXT_MISMATCH`.
Coverage/lineage verifiers (`checks/lineage_coverage.py`) feed `EvidenceCompletenessReport`
(`lineage_ok`, `contract_coverage_ok`, `coverage_ratio`) → adjudication `INSUFFICIENT_EVIDENCE`.

### ApprovedKnowledge

Promotion gates (deterministic policy — LLM cannot authorize):

1. claim not disputed/rejected/superseded  
2. deterministic critical checks OK  
3. verification PASS  
4. red team completed  
5. adjudication PASS  
6. provenance present  
7. **CONSENSUS alone is insufficient**

Demotion preserves history (`status=DEMOTED`).

### Evidence graph

Nodes: PROJECT, RUN, SOURCE, EVIDENCE, CLAIM, …, CONFLICT, ADJUDICATION, …  
Edges: SUPPORTS, CONTRADICTS, TESTS, VERIFIED_BY, SUPERSEDES, PART_OF, CREATED_IN, CITES, DERIVED_FROM, …  

Integrity: no dangling edges, no unknown types, no cross-project links except `SAME_AS`, no bidirectional `SUPERSEDES`, no cycles on acyclic edge types, VERIFICATION↔CLAIM for TESTS/VERIFIED_BY.

### Query API (`EvidenceQueryService`)

- `find_supporting_evidence` / `find_contradicting_evidence`  
- `find_verification_chain` / `find_decision_path` / `find_conclusion_dependencies`  
- `find_claim_history` / `find_run_evidence`  
- `get_project_timeline`  
- `compare_runs(run_a, run_b)`

### Philosophy

```text
Raw LLM output → Structured Proposal → Validation → Evidence → Graph → Policy → Approved Knowledge
```

LLM proposes. Deterministic layer validates. Policy authorizes. Agents never write ApprovedKnowledge or Decision ACCEPTED state without gates.

### Storage

JSON files + indexes (swappable later via `KnowledgeRepository` / `EvidenceRepository` / `RunRepository` protocols). No Neo4j in V2.1.

### ResearchProvider

Pipeline (not a stub-only interface): `search` / `fetch` / `resolve_source` / `research`.

`research()` returns `ResearchResult` (query, sources, evidence, findings, metadata).
Search backends are a separate `SearchProvider` (`mock`, `replay`, `web`).
`mock://` remains `STUB`. Provenance: Claim → Evidence (`SUPPORTS`) → Source (`DERIVED_FROM`) + Claim `CITES` Source.

Details: [research-pipeline.md](research-pipeline.md).

### Computation artifacts

`ComputationArtifact` (V2.4b/V2.4c/V2.5/V2.6) carries `code_hash` / `input_hash` / `environment_hash` / `computation_hash` (Docker adds `image_digest`; in-process solvers set `sandbox_backend=in_process`). V2.6 adds optional `calculation_spec_id`, `declared_outputs`, `task_id` (contract sidecar merge on load). Graph types stay `CALCULATION` / `SIMULATION` / `ASSUMPTION` — no `PHYSICS_RESULT` node. See [compute-sandbox.md](compute-sandbox.md), [docker-sandbox.md](docker-sandbox.md), [engineering-simulation.md](engineering-simulation.md), [benchmark-integrity.md](benchmark-integrity.md).

### Engineering verification (V2.2)

Quantitative claims carry `verification_spec` (or legacy `math_check`). `DeterministicVerifier` uses Pint + an AST interpreter. Results are `GraphNodeType.CHECK` nodes (`TESTS` / `VERIFIED_BY` → CLAIM). V2.6: claims without computation provenance cannot enter accepted synthesis results. See [engineering-verification.md](engineering-verification.md).

### Record/replay

`fixtures/llm/{role}/*.json` + `ReplayProvider` for deterministic tests.

V2.4a: replay fixtures may include routing metadata (`model`, `routing_policy_version`, `routed_model`). Same routing + same fixture → same response, no network. LLM invocation provenance lives under `.runs/<run_id>/llm/invocations.jsonl` (existing RunStore, not a second log). Different model ids are **not** `INDEPENDENT_EVIDENCE`. See [multi-model-routing.md](multi-model-routing.md).
