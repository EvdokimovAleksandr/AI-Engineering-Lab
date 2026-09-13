# Architecture — AI Engineering Lab (post V2 P0/P1)

> Исторический аудит: [architecture-audit.md](architecture-audit.md)  
> Целевая модель: [architecture-v2.md](architecture-v2.md)  
> Что реализовано: [architecture-v2-implementation.md](architecture-v2-implementation.md)

## Цель

Повышать вероятность **корректного** инженерного результата за счёт декомпозиции, **deterministic checks**, независимой проверки (blind ReviewBundle), red team и adjudication — не за счёт объёма текста или числа агентов.

## Фактическая оркестрация

`LabRuntime` исполняет **валидированный `TaskGraph`**, а не таблицу стадий как DAG.

0. **ScopeResolver / Scope gate (V2.8+, не AgentRole):** `original_problem` неизменен → problem kind + Known/Required frame → Clarification HITL при blocking Required → locked scope + `EngineeringContract` **до** TaskRouter/Planner
1. Planner (`static` | `llm`) → `TaskGraphProposal` (LLM is a proposal only; invalid graphs retry once then recover via `StaticPlanner` + TaskRouter profile — same validator, no role remapping)
2. Deterministic `validate_task_graph` (DAG, roles, schemas, budget, independence, optional routing, **contract binding**)
3. Ready-set execution (fan-out / fan-in); each agent call goes through `LLMRouter` + `RoutingPolicy`
4. Research recovery (V2.8, отдельно от planner recovery): empty/filtered search → bounded query refinement → evidence sufficiency. Provider error ≠ «доказательств не существует»
5. На review-узлах: `DeterministicCheckReport` → frozen `ReviewBundle` → **Verification ∥ Red Team** → `Adjudication`
6. Evidence completeness (V2.6): CalculationSpec + relevant ComputationArtifact + non-empty required checks
7. PASS → grounded synthesis | иначе → honest gated report (SIMPLE) или `IterationController` + `IterationPolicy` (graph revision vN)

`COMPLETED` означает только техническое завершение; `engineering_outcome` / adjudication — инженерный итог.

### ExecutionContext / CONTEXT_MISMATCH (PR-01)

Каждый исполняемый срез несёт неизменяемый `ExecutionContext`:
`project_id`, `investigation_id` (сегодня = имя project-папки), `task_id`, `run_id`, `contract_version`
(из активного `EngineeringContract.version`, иначе `"unset"`).

CalculationSpec, Claim, ComputationArtifact, ReviewBundle, ResearchResult (и SimulationSpec при runtime-stamp) биндятся к этому контексту. Перед исполнением / attach — жёсткая проверка; расхождение → `CONTEXT_MISMATCH` (`ContextMismatchError`), без silent remapping и без LLM-repair. Resume сохраняет project/run/investigation; `task_id` может смениться на следующий узел графа.

`STAGE_ROLES` — stage→roles metadata / источник `StaticPlanner`, **не** execution dependency graph.

### EngineeringContract (PR-03)

`EngineeringContract` — **источник истины** о том, что расследование должно установить (не узел TaskGraph).

Строится из `InvestigationScope` (V2.8 Scope Gate / Clarification HITL) — отдельного опросника нет.

| Статус | Смысл |
|--------|--------|
| `DRAFT` | Черновик; pipeline research/calculation/simulation **не** стартует |
| `NEEDS_CLARIFICATION` | Та же HITL-пауза, что у scope; pipeline **запрещён** |
| `READY` | Достаточно определено для расследования (не «всё известно») |
| `LOCKED` | Неизменяем для текущего run; материальное изменение → новый `version` |

**Правило pipeline:** стадии, которым нужен контракт, идут только при `READY` или `LOCKED`. Нелегальные переходы и мутация `LOCKED` — fail loud (`CONTRACT_NOT_READY` / `CONTRACT_LOCKED` / `ILLEGAL_CONTRACT_TRANSITION`).

`ExecutionContext.contract_version` берётся из активного контракта (`version`), а не остаётся `"unset"`, когда контракт есть. CalculationSpec / TaskGraph.metadata могут нести тот же `contract_version`; чужой stamp → mismatch.

### ScopeResolver + problem kind (PR-04)

Обязательная стадия (не `AgentRole`): `original_problem` → **ScopeResolver** → `EngineeringContract`.

| `ProblemKind` | Смысл |
|---------------|--------|
| `CLOSED_NUMERIC` | Закрытая численная задача (стержень Ø/F/d×2 → stress ratio; heater duty) |
| `PARAMETRIC` | Параметрический sweep / «как функция от» |
| `DESIGN` | Подбор / проектирование |
| `RESEARCH_REVIEW` | Обзор подходов / feasibility (silk industrial methods) |
| `EXPERIMENTAL` | Протокол эксперимента |
| `OPEN_ENDED` | Вопрос без locked Required → `NEEDS_CLARIFICATION` |

ScopeResolver заполняет кадр **Known / Unknown / Required / Optional / assumption candidates** (на `InvestigationScope` и контракте). HITL спрашивает **только Required**, блокирующие READY (например `load type: axial / bending / combined?`), без второго опросника.

`TaskGraph = f(EngineeringContract)`: после планирования граф и задачи штампуются `contract_version` / `investigation_id` / `required_outputs` / acceptance. Validator (`CONTRACT_BINDING_VIOLATION`) отвергает mismatch и orphan-узлы при `LOCKED`. Policy только **raise** по kind (`RESEARCH_REVIEW` → RESEARCH floor; `CLOSED_NUMERIC` предпочитает SIMPLE).

Артефакты: `planner/engineering_contract.json` + `engineering_contract_v{N}.json`, snapshot в `RunManifest`.

### Claim / Evidence lineage + coverage (PR-05)

- `Claim.support_status` (`PROPOSED`…`SUPPORTED`…`REJECTED`) + `evidence_ids` / `calculation_ids` / `assumption_ids`
- `EvidenceType` на `EvidenceRecord` (совместим с `SourceTrustTier` / graph EVIDENCE)
- Deterministic verifiers: `checks/lineage_coverage.py` → `EvidenceCompletenessReport.lineage_ok` /
  `contract_coverage_ok` / `coverage_ratio` / `covered_outputs` / `missing_outputs`
- Fail → `INSUFFICIENT_EVIDENCE`; synthesis не принимает `UNVERIFIED` / bare `PROPOSED`

### IterationController (PR-06)

`orchestrator/iteration_policy.py` — один контур: **IterationPolicy** (стадия re-entry) +
**IterationController** (CONTINUE / REPLAN / ASK_USER / STOP_*).

Правила:

1. На каждой non-PASS (и PASS) adjudication фиксируется прогресс: `coverage_ratio`,
   missing required outputs, `FailureClass`.
2. N подряд без прогресса (default `runtime.iteration.no_progress_limit=3`: те же missing,
   тот же `failure_class`, нет роста coverage) → **REPLAN один раз** (re-entry с
   `DECOMPOSITION`).
3. После REPLAN снова нет прогресса → **STOP_INSUFFICIENT_EVIDENCE** (или **ASK_USER**,
   если `runtime.iteration.hitl_on_no_progress=true`). Не ждём `BUDGET_EXCEEDED`.
4. Рост coverage / сужение missing → **CONTINUE**, streak сбрасывается.
5. `STOP_BUDGET` — только при реальном budget exceeded; `STOP_*` пишет честный
   `engineering_outcome=INSUFFICIENT_EVIDENCE`, не fake PASS.

`FailureClass`: USER_INPUT | SCOPE | PLANNING | CONTEXT | UNIT | CALCULATION |
SIMULATION | RESEARCH | VERIFICATION | BUDGET | PROVIDER | INFRASTRUCTURE
(маппинг с `LabErrorCode` / `ResearchOutcome` / reason text).

Артефакт run: `.runs/<id>/reviews/iteration_progress.json`.

Подробности: [taskgraph-planner.md](taskgraph-planner.md), [benchmark-integrity.md](benchmark-integrity.md). Симуляция: [engineering-simulation.md](engineering-simulation.md). UI: [ui.md](ui.md).

```text
                    User
                      │
               Simple Local UI
                      │
                      ▼
              original_problem
                      │
                      ▼
           ScopeResolver (problem kind)
                      │
            Clarification HITL?
                      │
                      ▼
             EngineeringContract
                      │
                      ▼
                 Task Router
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
 Evidence recovery
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

### Scope Gate

The laboratory does not automatically convert ambiguous user intent into a definitive engineering specification.

### Clarification Gate

When ambiguity materially changes the investigation, the run pauses for user input via existing HITL/`--resume` / `POST /api/runs/{id}/resume`.

### Research Recovery

A zero-result search triggers diagnostic/refinement before being treated as evidence insufficiency. Planner recovery (invalid DAG → StaticPlanner) is a different loop.

### Evidence Gate

Missing evidence is not silently converted into assumptions. Provider failures are `RESEARCH_PROVIDER_ERROR`, not “no evidence exists”.

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
| `orchestrator/` | LabRuntime, budget, adjudication, synthesis gate, IterationController / policy |
| `planner/` | TaskGraph proposal, deterministic validator, StaticPlanner / LLMPlanner |
| `simulation/` | SimulationSpec, SolverRegistry, UniaxialTensionSolver (V2.5) |
| `ui/` | Local HTML/HTTP adapter over LabRuntime (not a second orchestrator) |

## Поток данных

```
USER → original_problem → ScopeResolver → EngineeringContract / Clarification HITL
  → TaskRouter → Planner → validated TaskGraph (contract-bound) → LabRuntime
  → Research (+ recovery) / SimulationSpec / Solver / ComputationArtifact
  → deterministic checks → ReviewBundle
  → Verification ∥ Red Team → Adjudication
  → FAIL: IterationController (REPLAN/STOP) + IterationPolicy | PASS: SynthesisBundle → final_report.md
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
- **Dimension model (PR-02):** semantic `Dimension` (`length`, `area`, …) → reference SI unit → Pint dimensionality. `expected_dimensions` may use Dimension names, real units (`m`, `Pa`), or legacy SI letters (`L`, `L**2`) mapped explicitly to LENGTH/AREA — never feed bare `L` to Pint as “length” (Pint `L` = litre). See [benchmark-integrity.md](benchmark-integrity.md).
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
