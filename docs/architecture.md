# Architecture — AI Engineering Lab (post V2 P0/P1)

> Исторический аудит: [architecture-audit.md](architecture-audit.md)  
> Целевая модель: [architecture-v2.md](architecture-v2.md)  
> Что реализовано: [architecture-v2-implementation.md](architecture-v2-implementation.md)

## Цель

Повышать вероятность **корректного** инженерного результата за счёт декомпозиции, **deterministic checks**, независимой проверки (blind ReviewBundle), red team и adjudication — не за счёт объёма текста или числа агентов.

## Фактическая оркестрация

`LabRuntime` исполняет **валидированный `TaskGraph`**, а не таблицу стадий как DAG.

1. Planner (`static` | `llm`) → `TaskGraphProposal`
2. Deterministic `validate_task_graph` (DAG, roles, schemas, budget, independence, optional routing)
3. Ready-set execution (fan-out / fan-in); each agent call goes through `LLMRouter` + `RoutingPolicy`
4. На review-узлах: `DeterministicCheckReport` → frozen `ReviewBundle` → **Verification ∥ Red Team** → `Adjudication`
5. Evidence completeness (V2.6): CalculationSpec + relevant ComputationArtifact + non-empty required checks
6. PASS → grounded synthesis | иначе → honest gated report (SIMPLE) или `IterationPolicy` (graph revision vN)

`COMPLETED` означает только техническое завершение; `engineering_outcome` / adjudication — инженерный итог.

`STAGE_ROLES` — stage→roles metadata / источник `StaticPlanner`, **не** execution dependency graph.

Подробности: [taskgraph-planner.md](taskgraph-planner.md), [benchmark-integrity.md](benchmark-integrity.md). Симуляция: [engineering-simulation.md](engineering-simulation.md). UI: [ui.md](ui.md).

```text
                    User
                      │
               Simple Local UI
                      │
                      ▼
                  Problem
                      │
                      ▼
                TaskGraph Planner
                      │
               Structured Proposal
                      │
                      ▼
             Deterministic Validator
                      │
                      ▼
                 Approved Graph
                      │
       ┌──────────────┼────────────────┐
       ▼              ▼                ▼
   Research       Simulation       Calculation
                      │
                      ▼
             Engineering Model
                      │
                      ▼
               Solver Registry
                      │
                      ▼
                ComputeSandbox
                 ┌────┴────┐
                 ▼         ▼
              Local      Docker
                 │         │
                 └────┬────┘
                      ▼
             ComputationArtifact
                      │
                      ▼
            Deterministic Verification
                      │
                ┌─────┴─────┐
                ▼           ▼
          Verification    Red Team
                │           │
                └─────┬─────┘
                      ▼
                 Adjudication
                      │
                      ▼
                  Synthesis
                      │
                      ▼
                 Conclusion
```

## Слои

| Слой | Ответственность |
|------|-----------------|
| `core/` | Enums, модели (Claim versioning, RunManifest, ReviewBundle, Graph…) |
| `checks/` | DeterministicVerifier (Pint + AST) + legacy MathCheck |
| `llm/` | `MockProvider`, `CursorSDKProvider` (reasoning-only), `PolicyLLMRouter` |
| `tools/` | Registry + permissions + **trust_level** taint |
| `memory/` | ProjectStore, EvidenceStore, EvidenceGraph, RunStore, ReviewBundle |
| `agents/` | 6 ролей (без новых) |
| `workflows/` | FSM + transitions |
| `orchestrator/` | LabRuntime, budget, adjudication, synthesis gate, iteration policy |
| `planner/` | TaskGraph proposal, deterministic validator, StaticPlanner / LLMPlanner |
| `simulation/` | SimulationSpec, SolverRegistry, UniaxialTensionSolver (V2.5) |
| `ui/` | Local HTML/HTTP adapter over LabRuntime (not a second orchestrator) |

## Поток данных

```
USER → Simple Local UI (optional) → Problem
  → Planner → validated TaskGraph → LabRuntime
  → Research / SimulationSpec / Solver / ComputationArtifact
  → deterministic checks → ReviewBundle
  → Verification ∥ Red Team → Adjudication
  → FAIL: IterationPolicy | PASS: SynthesisBundle → final_report.md
```

Simulation details: [engineering-simulation.md](engineering-simulation.md). UI: [ui.md](ui.md).

## Final report gate

`final_report.md` строится из `SynthesisBundle` (accepted/disputed/rejected + V/RT + residual risks).  
LLM может добавить prose, но **не** определяет, что доказано.  
DISPUTED/FAIL/INCOMPLETE явно маркируются как недоказанные.

## Reproducibility

Каждый run: `projects/<name>/.runs/<run_id>/manifest.json` + immutable `computations/*.json`.

## Safety

- Python compute: `ComputeSandbox` — `LocalSubprocessSandbox` (process isolation) or optional `DockerSandbox` (container isolation). Not a fully secure “100%” sandbox — see [compute-sandbox.md](compute-sandbox.md) and [docker-sandbox.md](docker-sandbox.md)
- DeterministicVerifier: no `eval`/`exec`; Pint units; explicit tolerance
- Path jail в ProjectStore
- CursorSDK: **reasoning-only** temp cwd (не project root)
- Research tool output: `trust_level=EXTERNAL`, `data_not_instructions=true`
- `mock://` sources = STUB, cannot be FACT

## HITL

- Critical disputed red-team findings
- `AgentResult.hitl_request` → `AWAITING_HUMAN`
- Resume: `--resume` / same `run_id` in `project_state.json`
- Tests: `--auto-approve-hitl`

## CLI

```bash
python -m ai_lab run projects/spider_silk_industrial --provider mock
python -m ai_lab run projects/spider_silk_industrial --resume --auto-approve-hitl
python -m ai_lab plan projects/spider_silk_industrial --provider mock
python -m ai_lab research "spider silk spinning"
python -m ai_lab routing [--provider mock]
python -m ai_lab sandbox
```

Research CLI prints structured `query` / `sources` / `evidence` / `provenance`. See [research-pipeline.md](research-pipeline.md).

Model routing (V2.4a): [multi-model-routing.md](multi-model-routing.md). `LLMRouter` selects a validated `ModelConfig` per role; Verification ∥ Red Team may use different models. Different models are **not** `INDEPENDENT_EVIDENCE`.

## Knowledge layer (V2.1)

Claims are **run-scoped** (`.runs/<run_id>/claims/`). Agents use visibility policies (`CURRENT_RUN` / `PROJECT_HISTORY` / `APPROVED_KNOWLEDGE`). Evidence graph + query API live under `knowledge/`. See [knowledge-architecture.md](knowledge-architecture.md).

Quantitative claims: [engineering-verification.md](engineering-verification.md) (`Claim` → `VerificationSpec` → `CHECK` node).

Structured models: [engineering-simulation.md](engineering-simulation.md) (`SimulationSpec` → solver → `SimulationResult` → verifier). Local UI: [ui.md](ui.md).

General-purpose Python (untrusted) runs in [compute-sandbox.md](compute-sandbox.md) / [docker-sandbox.md](docker-sandbox.md). That path is not `DeterministicVerifier`.
