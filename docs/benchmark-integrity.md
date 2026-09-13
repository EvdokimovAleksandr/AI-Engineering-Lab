# Benchmark Integrity & Semantic Verification (V2.6 / V2.6.1)

Цель V2.6: сделать невозможным ложный инженерный PASS, когда LLM понимает задачу A,
исполняет computation B, verification пуст, а synthesis возвращает ответ из prose.

V2.6.1 — adversarial hardening той же архитектуры: policy lock, Pint dimensions,
required-output coverage, generic benchmark acceptance, orphan binding.

## Technical completion ≠ engineering success

| Понятие | Смысл |
|---------|--------|
| `final_state=COMPLETED` | Pipeline технически дошёл до конца |
| `engineering_outcome` | `PASS` / `FAIL` / `INSUFFICIENT_EVIDENCE` / `DISPUTED` |
| `AdjudicationStatus.PASS` | Все mandatory evidence gates пройдены |

`COMPLETED` + `INSUFFICIENT_EVIDENCE` — нормальный исход (например, нерелевантный compute).

Understanding пишет `required_outputs` / `expected_dimensions`; они **блокируются** в
`VerificationPolicy` и не могут быть сняты simulation LLM. Оффтопный, но
self-consistent compute (стержень / KV-cache при задаче на heater `power`) не даёт PASS.

## Engineering PASS requires

1. required outputs known and locked (understanding → `VerificationPolicy`);
2. required outputs covered (claim ← computation ← verification);
3. relevant computation exists (`declared_outputs`, Pint dimensions);
4. deterministic verification executes (non-empty checks);
5. required checks pass;
6. benchmark acceptance contract passes (when registered);
7. accepted claims have provenance;
8. synthesis is grounded (`SynthesisBundle`).

V2.8: engineering PASS also requires locked `SCOPE_RESOLVED`/`SCOPE_ASSUMED` and, when research is required, sufficiency that is not `RESEARCH_EMPTY` / `FILTERED` / `PROVIDER_ERROR` / `PARTIAL`. Missing evidence is an **evidence gap**, not an assumption. LLM synthesis cannot decide that research was sufficient.

## Pipeline

```text
                         Problem
                            │
                            ▼
                     Understanding
                     required_outputs ──► Policy lock (immutable)
                            │
                            ▼
                     Task / TaskGraph
                            │
                            ▼
                    CalculationSpec
                            │
                 ┌──────────┴──────────┐
                 │                     │
                 ▼                     ▼
           LLM proposal          deterministic
                                 validation
                 │                     │
                 └──────────┬──────────┘
                            ▼
                    Compute execution
                            │
                            ▼
                   ComputationArtifact
                            │
                     relevance gate
                            │
                            ▼
                  DeterministicVerifier
                            │
                     completeness
                     + coverage
                     + acceptance
                            │
                            ▼
                       Adjudication
                            │
                       PASS only if
                       all gates pass
                            │
                            ▼
                   Verified Claims
                            │
                            ▼
                    Grounded Synthesis
```

## CalculationSpec (contract)

Связывает `TaskSpec` (calculation) с `ComputationArtifact`:

- `objective`, `required_inputs`, `required_outputs`
- `expected_dimensions` — **semantic Dimension / unit token**, не string equality:
  - предпочтительно `length` / `area` / `pressure` / `power` (`Dimension` enum) или реальные единицы (`m`, `m**2`, `Pa`, `W`);
  - legacy SI-буквы Chief (`L`, `L**2`, `M`, `T`) явно мапятся на LENGTH/AREA/MASS/TIME **до** Pint;
  - Pint `L` = litre (volume): на стороне *actual* `declared_outputs` буква `L` остаётся литром
- `expected_relations` (advisory / untrusted)
- `verification_required`, `minimum_checks`
- `trusted_fields` — поля, которые LLM не может ослабить через `VerificationPolicy`

LLM может **предложить** spec; policy overlay:

- union-locks `required_outputs` (LLM cannot remove);
- **overwrites** `expected_dimensions` for locked keys (LLM cannot replace `W` with `GiB`);
- rejects vague names (`engineering_result`, `answer`, …).

Stdout сам по себе **не** engineering result — нужны `declared_outputs`.

## Relevance gate

`validate_computation_against_spec(computation, calculation_spec)`:

- missing required outputs → invalid
- dimension mismatch (`power/W` vs `memory_gib/GiB`; `W`≡`kW`≡`J/s`; `J`≠`W`;
  `length`/`L`≡`m`; `L**2`/`area`≡`m**2`; `length`≢`liter`) → invalid
- junk formula strings as units → invalid; Dimension names (`length`) are valid
- empty `declared_outputs` → invalid (даже если stdout красивый)
- orphan artifacts (wrong / missing `calculation_spec_id`) do not satisfy the spec

## Evidence completeness + coverage

Перед adjudication: `evaluate_evidence_completeness(...)`.

Проверяет:

- required calculation / ComputationArtifact
- computation relevant
- verification report не пуст (если `verification_required`)
- required checks executed and passed
- quantitative claims имеют computation + math_check/verification_spec
- **required_output_coverage** — каждый locked output имеет verified claim, связанный с
  computation, который объявляет этот output (`covers_outputs` / statement)
- **acceptance_passed** — generic benchmark acceptance (если контракт задан)
- **lineage_ok / contract_coverage_ok** (PR-05) — claim/evidence/computation разделяют
  investigation/run/contract; required outputs контракта имеют claim+evidence lineage.
  `coverage_ratio` / `covered_outputs` / `missing_outputs` питают IterationController (PR-06).
  Fail → `INSUFFICIENT_EVIDENCE`, не silent PASS.

Пустой `DeterministicCheckReport` **никогда** не даёт PASS при `verification_required`.

## Generic benchmark acceptance

`BenchmarkExpectation.acceptance_outputs` / `acceptance_inputs` — не `if name == "simple_heater"`.

Для `simple_heater` контракт выражает:

- required output `power` (aliases: `heater_electrical_power`, …)
- dimension: power (`W` / `kW` / …)
- magnitude band ≈ `[3.0, 3.5] kW` (как в `evaluation.md`)
- optional input oracle: wrong `m` / `ΔT` / `t` / `loss` fail when present

Self-consistent wrong physics (t=60 s → ~98 kW) fails the band.
Accidental numeric match with wrong inputs fails the input oracle.

## Adjudication

Порядок:

1. Deterministic critical FAIL → `FAIL` (нельзя upgrade)
2. Evidence incomplete / missing verification / failed acceptance → `INSUFFICIENT_EVIDENCE`
3. Required checks failed → `FAIL`
4. Red team unresolved critical → `DISPUTED` / `FAIL`
5. Иначе → `PASS`

Не: `if no_failures: PASS`.

## Synthesis grounding

`build_synthesis_bundle` + `validate_synthesis_grounding`:

- quantitative `accepted_claims` / `verified_results` только из claims с passed deterministic check
- LLM narrative (`summary`) не может создать accepted quantitative claim
- synthesis cannot upgrade `INSUFFICIENT_EVIDENCE` → `PASS`
- PR-05: `UNVERIFIED` / `REJECTED` / `CONTRADICTED` никогда не accepted; `PROPOSED` без
  evidence lineage + verified computation — тоже нет
- Understanding lock snapshot is run-scoped; synthesis writes `chief_synthesis_notes.json`

## Semantic drift examples (must NOT engineering PASS)

| Drift | Typical artifact |
|-------|------------------|
| KV-cache | `memory_gib` / GiB |
| Aluminum expansion | `delta_L` / m |
| Heat flux | `heat_flux` / W/m² |
| Electrical resistance | Ω (wrong role) |
| Energy-only | J declared as power |

Successful Python `exit=0` of unrelated code is **not** evidence.

## Budget accounting

- Usage регистрируется в момент LLM completion (`record_llm_usage`)
- `tokens_used = 0` означает известный ноль; `tokens_used = null` + `tokens_unknown` — usage неизвестен
- `None → 0` запрещено
- `finish_manifest` записывает **live** `RunBudget`

## Trusted vs untrusted

| Untrusted (LLM) | Trusted / validated |
|-----------------|---------------------|
| objective / equations proposal | verification policy |
| claimed outputs / narrative | required output dimensions (policy wins) |
| model reasoning | mandatory checks, sandbox, budget, acceptance |

LLM не повышает trust level и не может удалить required checks/outputs,
переписать deterministic status, или объявить нерелевантный compute relevant.

## Adversarial fixtures (mock)

`MockProvider(simulation_fixture=...)`:

| Fixture | Expected |
|---------|----------|
| `heater_correct` | PASS |
| `heater_wrong_math` | FAIL |
| `kv_cache_unrelated` | NOT PASS |
| `irrelevant_aluminum` | NOT PASS |
| `irrelevant_heat_flux` | NOT PASS |
| `wrong_inputs` / `wrong_units` / `wrong_formula` | NOT PASS |
| `correct_prose_wrong_compute` | NOT PASS |
| `policy_lock_attack` | NOT PASS |
| `accidental_numeric_match` | NOT PASS |

## PR-07 behavioral coverage

Benchmarks / scenarios now also judge **refusal and stop behavior**, not only
numeric PASS:

| Case | Expected behavior | Notes |
|------|-------------------|-------|
| `simple_heater` | PASS | closed calc; default mock fixture `heater_correct` |
| `ambiguous_rod_strength` / scenario `ambiguous_engineering` | NEEDS_CLARIFICATION | Required `load_type` |
| scenario `context_isolation` | FAIL + FailureClass.CONTEXT | sofa+rod contamination |
| scenario `units` | PASS (oracle) | length≡m; length≢litre |
| scenario `budget_control` | INSUFFICIENT_EVIDENCE | IterationController stop, no token burn |
| `spider_silk_review` | ORCHESTRATION_ONLY | STUB/empty research ≠ PASS |

Evaluate reports include `details.execution_mode` (`MOCK` / `LIVE_CURSOR` /
`REPLAY`) and a `failure_class` category that **reuses** PR-06 `FailureClass`
(do not fork the enum).

## Invariants (V2.6.1)

1. Technical completion ≠ engineering success  
2. No required verification ≠ PASS  
3. No required computation ≠ PASS  
4. Unrelated computation ≠ valid engineering result  
5. LLM prose cannot create verified quantitative claims  
6. Verified computation outranks conflicting LLM prose  
7. PASS requires complete provenance + required-output coverage  
8. Unknown token usage ≠ zero usage  
9. Adjudication cannot override deterministic critical failures  
10. No benchmark-name hardcoding in runtime gates  
11. Locked dimensions cannot be overridden by CalculationSpec  
12. Scalar match alone ≠ acceptance when input oracle fails  
13. Orphan computation (unbound spec id) ≠ acceptance  
14. Synthesis cannot increase evidence status  

## Modules

- `ai_lab.checks.calculation_contract` — spec parse, relevance, completeness, coverage
- `ai_lab.checks.units` — `Dimension` enum, legacy SI map, Pint `units_compatible` / `convert_magnitude`
- `ai_lab.benchmark.acceptance` — generic acceptance oracle
- `ai_lab.orchestrator.adjudication` — completeness-aware gate
- `ai_lab.orchestrator.synthesis` — grounded accepted claims
- `ai_lab.agents.simulation` — CalculationSpec + declared_outputs + covers_outputs
- `ai_lab.agents.chief_engineer` — run-scoped understanding lock
- `ai_lab.memory.run_store` — contract sidecar; live budget; review snapshots
- `ai_lab.orchestrator.budget` — unknown ≠ zero

See also: [engineering-verification.md](engineering-verification.md), [benchmarks.md](benchmarks.md),
[knowledge-architecture.md](knowledge-architecture.md), [taskgraph-planner.md](taskgraph-planner.md).

