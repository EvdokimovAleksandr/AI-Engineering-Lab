# TaskGraph Planner (V2.3)

LLM may **propose** work. It must not **execute** work.

```text
Problem
  → Planner (StaticPlanner | LLMPlanner)
  → TaskGraphProposal          # untrusted
  → parse (forbidden fields)
  → TaskGraph
  → deterministic validator
  → approved TaskGraph
  → LabRuntime
```

`STAGE_ROLES` remains a **stage → roles table**. It is not the execution DAG. `StaticPlanner` compiles the default pipeline into a `TaskGraph`. `LabRuntime` loads that graph, validates it, then walks ready tasks.

V2.6: calculation tasks bind to a `CalculationSpec`; empty deterministic checks cannot PASS for quantitative engineering. See [benchmark-integrity.md](benchmark-integrity.md).

## Why

The MVP scheduler was a hidden FSM: `STAGE_ROLES[state]` plus `advance()`. Chief `follow_up_tasks` were ignored. A planner that *ran* agents would give the model execution authority.

V2.3 splits three jobs:

| Layer | May decide | May not |
|-------|------------|---------|
| Planner | Which tasks *might* be needed | Execute, write files, spend budget, mark success |
| Validator | Whether the DAG is legal | Invent tasks |
| LabRuntime | How to run an **approved** graph | Trust an unvalidated proposal |

## TaskSpec

One existing type, evolved — not a second competing model.

| Field | Role |
|-------|------|
| `task_id` | Stable identity (existing name; not a second `id`) |
| `role` | `AgentRole` for `task_kind=agent`; unset for runtime-owned nodes |
| `task_kind` | `agent` \| `deterministic_check` \| `adjudication` \| `model_build` \| `simulation` \| `simulation_verification` |
| `objective` | What to do (plain text, including hostile text) |
| `inputs` | Artifact ids / upstream task ids (`input_node_ids` accepted on ingest) |
| `output_schema` | Closed registry (`research_findings`, `check_report`, …) |
| `depends_on` | DAG edges |
| `independence_group` | Parallel isolation (V∥RT use `independent_review`) |
| `budget_slice` | Planned caps; **RunBudget** is still the only runtime ledger |
| `state_context` | Stage-table compatibility metadata |
| `priority` | Tie-break among ready tasks |
| `allowed_tools` | Must be known registry names |
| `review_bundle_path` | Set by runtime only — forbidden in planner proposals |

There are **no** `command` / `script` / `python_code` / `shell` / `cwd` / `path` fields. Extra keys are forbidden. Computation still goes through `python.execute` → ComputeSandbox **or** a registered `EngineeringSolver` (V2.5). The planner cannot attach `network=allow`, host cwd, or an arbitrary solver import. See [compute-sandbox.md](compute-sandbox.md), [engineering-simulation.md](engineering-simulation.md).

## TaskGraph

```text
TaskGraph(graph_id, tasks, version, supersedes?, reason?, metadata)
```

JSON-serializable. Stored under the existing run store:

```text
projects/<name>/.runs/<run_id>/planner/
  proposal.json
  task_graph.json
  task_graph_v1.json
  validation.json
  executions.json
```

`RunManifest.task_graph_id` / `task_graph_hash` / `task_graph_version` bind the plan to the run.

`task_graph_hash` is SHA-256 of a canonical object (sorted keys, tasks sorted by `task_id`). JSON whitespace and list order do not change the hash. Changing `objective`, `depends_on`, `role`, `output_schema`, or `budget_slice` does.

## Validator

`validate_task_graph(graph, context) → TaskGraphValidationResult`

Deterministic checks:

- unique task ids, legal `graph_id`, required fields
- known roles / kinds / output schemas / tools
- dependencies exist, no self-edge, DAG (Kahn + leftover = cycle)
- inputs are known artifacts **or** task ids listed in `depends_on`
- planned `budget_slice` sums (or policy defaults) vs `RunBudget` → `BUDGET_EXCEEDED`
- independence: Verification and Red Team must not depend on author tasks or on each other; they share `independence_group=independent_review`
- optional routing: if `TaskGraphValidationContext.routing_policy` is set, `independence` YAML constraints are checked against `RoutingPolicy` (`ROUTING_VIOLATION`)

List order of `tasks` must not change validation or topological order. Ready ties: higher `priority`, then `task_id`.

## Planners

### StaticPlanner

No LLM. Always the same backbone (research → … → deterministic_verify → verification ∥ red_team → adjudication → synthesis). Used for demo/regression (`runtime.planner: static`).

### LLMPlanner

Calls the LLM with schema `TaskGraphProposal`. System prompt states that `<UNTRUSTED_DATA>` is **data**, not instructions. The planner does not call tools, write files, or mutate `RunBudget`.

Pipeline:

```text
LLM JSON → TaskGraphProposal → parse_proposal → validate_task_graph
if not ok: stop
LabRuntime.execute(graph)
```

`follow_up_tasks` from Chief remain advisory and are **not** executed.

## Failure semantics

`TaskStatus` (`PENDING` / `SUCCESS` / `FAILED` / `BLOCKED` / `SKIPPED` / `CANCELLED` / `HITL_REQUIRED`) is **not** `CheckStatus` or `VerificationStatus`.

Adjudication may complete (`TaskStatus=SUCCESS`) with `AdjudicationStatus=FAIL`. That is a verdict, not a crashed task. Synthesis is skipped; `IterationPolicy` still chooses the re-entry **stage**. V2.3 materializes that as `TaskGraph` vN (`supersedes`, `reason`). It does not add a second iteration engine.

## Independence

Planner cannot turn:

```text
verification → red_team
```

into a legal graph. Both review tasks depend only on `deterministic_verify` (ReviewBundle / checks), run in parallel, and never see each other's reports.

V2.4a: the same `independence_group` is a routing input. A policy may require `verification` and `red_team` to use different `ModelConfig`s. That is model diversity, not `INDEPENDENT_EVIDENCE`. See [multi-model-routing.md](multi-model-routing.md).

## HITL

Existing HITL (`AgentResult.hitl_request`, disputed red team, `--resume`) is unchanged.

Optional plan gate: `runtime.hitl_on_plan` or `TaskGraphProposal.requires_human_approval`. Human approves **structure**, not internal runtime blobs.

```bash
python -m ai_lab plan projects/spider_silk_industrial --provider mock
```

Creates a run, writes planner artifacts, prints the DAG, **does not** run research/compute.

```bash
python -m ai_lab run projects/spider_silk_industrial --provider mock
```

Uses the same validated TaskGraph as the execution schedule.

## Provenance

Evidence graph gains a `TASK_GRAPH` node (`PART_OF` the `RUN`). Claims/decisions created during a task get `created_by=task_id` and `DERIVED_FROM` the graph node. Executions list `claim_ids` / `artifact_paths` per task. From a decision you can recover which tasks produced the supporting claims.

## Deterministic guarantees (no LLM)

- DAG / cycle detection
- topological order
- hash / JSON round-trip
- budget and independence rejection
- StaticPlanner output
- validator `ok` bit (LLM cannot set it)

## Not in V2.3

Adaptive multi-revision planner, conditional tasks, RAG, physics engines, UI, autonomous experiments. Compute isolation is a **runtime** concern: V2.4b local subprocess + V2.4c Docker — see [compute-sandbox.md](compute-sandbox.md) / [docker-sandbox.md](docker-sandbox.md).

## Example (static backbone)

```text
understanding → decomposition → research → hypothesis → analysis
  → calculation → simulation → deterministic_verify
       ├─ verification
       └─ red_team
              └─ adjudication → synthesis
```
