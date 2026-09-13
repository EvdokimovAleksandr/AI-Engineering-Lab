# AUDIT REPORT — PR-C Hard ExecutionContext on New Writes

**Branch:** `feat/lab-modernization-p0-p7`  
**Date:** 2026-09-13  
**Mode:** MOCK (`--provider mock`)  
**Scope:** PR-C only (strict identity on new writes).  
Claim promotion / shaft mock oracle / empty-coverage heuristics — **Deferred**.

---

# Implementation

Новые persist-пути требуют полный `ExecutionContext`:
`project_id`, `investigation_id`, `task_id`, `run_id`.

- отсутствует любое поле → hard fail `MISSING_EXECUTION_CONTEXT`;
- **нет** auto-fill `None` из store/run_id на write-path;
- legacy допускается только явно: `legacy_migrate=True` или
  `migrate_project_knowledge` / чтение старых JSON с диска;
- soft-match (`require_context_match` пропускает пустые поля) остаётся
  **только для read/validate**, не для authorize write.

Проводка:

1. `RunStore.save_computation` / `EvidenceStore.save_claim` /
   `JsonKnowledgeRepository.save_claim` — gate перед записью.
2. Sandbox (`local`/`docker`) + `python.execute` штампуют identity
   **до** первого immutable write; ephemeral math-check использует
   `persist_computation=False`.
3. Agents (simulation/research/chief/theorist) и
   `ingest_research_result` передают полный binding.
4. `CalculationSpec` proposal без полного контекста →
   `MISSING_EXECUTION_CONTEXT` (кроме `legacy_migrate=True`).

---

# Files changed

| File | Change |
|------|--------|
| `src/ai_lab/core/enums.py` | `MISSING_EXECUTION_CONTEXT` |
| `src/ai_lab/core/execution_context.py` | `MissingExecutionContextError`, `require_full_*` / `require_write_*` |
| `src/ai_lab/memory/run_store.py` | strict `save_computation` |
| `src/ai_lab/memory/evidence_store.py` | no auto-fill; strict save |
| `src/ai_lab/knowledge/json_knowledge.py` | strict claim write + supersede inherit |
| `src/ai_lab/knowledge/__init__.py` | no soft-fill in KnowledgeService |
| `src/ai_lab/knowledge/ingest_research.py` | require + stamp identity |
| `src/ai_lab/sandbox/models.py` | SandboxContext identity fields |
| `src/ai_lab/sandbox/local.py` / `docker.py` | stamp on artifact create |
| `src/ai_lab/tools/python_exec.py` | identity kwargs + persist gate |
| `src/ai_lab/tools/research.py` | pass context into ingest |
| `src/ai_lab/agents/simulation.py` | pass context to execute; claim/spec gates |
| `src/ai_lab/agents/research.py` / `chief_engineer.py` / `theorist.py` | stamp claims |
| `src/ai_lab/checks/calculation_contract.py` | strict parse (legacy opt-in) |
| `src/ai_lab/simulation/*` + `runtime.py` | solver/claim context; math-check non-persist |
| `src/ai_lab/orchestrator/iteration_policy.py` | map error → CONTEXT |
| `src/ai_lab/cli_research.py` | CLI ingest identity |
| `tests/test_execution_context_pr_c.py` | **new** exact PR-C tests |
| `tests/*` (fixtures) | full identity on Claim/Artifact constructors |
| `docs/audit-pr-c-execution-context.md` | этот отчёт |

---

# Tests

```text
.venv/Scripts/python.exe -m pytest \
  tests/test_execution_context_pr_c.py \
  tests/test_method_compatibility_pr_b.py \
  tests/test_adversarial_integrity.py \
  tests/test_scope_resolver_pr04.py \
  tests/test_context_isolation.py \
  tests/test_benchmark_integrity.py -q
# 95+ related passed; PR-C file: 6/6
```

Точные имена в `tests/test_execution_context_pr_c.py`:

- `test_new_computation_requires_full_context`
- `test_new_claim_requires_full_context`
- `test_new_evidence_requires_full_context`
- `test_missing_context_cannot_be_saved`
- `test_legacy_migration_is_explicit`
- `test_existing_legacy_artifact_can_be_read_without_relaxing_new_writes`

**Physical disk (mandatory):** на `simple_heater` run
`run_df15ebdef0af` и ad-hoc `run_39482acc5410` JSON под
`.runs/<run_id>/computations/*.json`, `claims/*_v*.json`,
`planner/calculation_specs/*.json` содержат все четыре identity-поля.
In-memory-only недостаточно — проверено чтением файлов.

---

# Adversarial E2E audit

## 1) simple_heater

**Command:** `python -m ai_lab benchmark run simple_heater --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_df15ebdef0af` |
| final_state | COMPLETED |
| engineering_outcome | **PASS** |
| contract status | LOCKED |
| problem_kind | CLOSED_NUMERIC |
| required fields | `[]` |
| produced calculation method | `calculate_heater_power` |
| domain | thermal / thermal_heating |
| claims support_status | PROPOSED |
| evidence completeness | `method_compatible=True`, `coverage_ratio=1.0` |
| context identity completeness | **full** on computation + claims + CalculationSpec (disk) |
| **verdict** | 🟢 formal+semantic OK; PR-A/B intact; PR-C identity OK |

## 2) ambiguous_rod_strength (initial)

**Command:** `python -m ai_lab benchmark run ambiguous_rod_strength --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_e3691e19dc1c` |
| final_state | AWAITING_HUMAN |
| engineering_outcome | null |
| contract status | NEEDS_CLARIFICATION |
| problem_kind | OPEN_ENDED |
| required fields | `[load_type]` |
| calculation | none |
| context identity | n/a (no computation writes) |
| **verdict** | 🟢 PR-A first gate OK |

## 3) ambiguous_rod + axial resume (PR-A path)

**Command:** pytest
`tests/test_scope_resolver_pr04.py::test_open_ended_design_cannot_reach_engineering_pass_after_one_answer`

| Field | Value |
|-------|--------|
| final_state | **AWAITING_HUMAN** |
| engineering_outcome | null |
| contract status | **NEEDS_CLARIFICATION** |
| problem_kind | **DESIGN** |
| required fields | non-empty (`strength_metric` / geometry / constraint stage) |
| known | `{load_type: axial}` |
| calculation | **none** |
| **verdict** | 🟢 PR-A retained |

## 4) shaft_design

**Command:** `python -m ai_lab benchmark run shaft_design --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_b01eedac1f0f` |
| final_state | COMPLETED |
| engineering_outcome | **INSUFFICIENT_EVIDENCE** |
| contract status | LOCKED |
| problem_kind | DESIGN |
| required fields | `[]` |
| produced calculation method | mock `calculate_fiber_stress` → **rejected** (`incompatible_*`) |
| domain | `mechanical_shaft` |
| claims support_status | PROPOSED |
| evidence completeness | method gate blocks PASS |
| context identity completeness | **full** on written computation/claims |
| **verdict** | 🟢 PR-B retained; no false PASS |

## 5) spider_silk_review

**Command:** `python -m ai_lab benchmark run spider_silk_review --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_ed24370c38f8` |
| final_state | COMPLETED |
| engineering_outcome | INSUFFICIENT_EVIDENCE |
| contract status | LOCKED |
| problem_kind | RESEARCH_REVIEW |
| produced calculation method | `calculate_fiber_stress` (allowed for biomaterials) |
| domain | biomaterials |
| claims support_status | PROPOSED |
| context identity completeness | **full** on comps/claims/specs |
| **verdict** | 🟢 research honesty OK; research ingest identity OK |

## 6) ad-hoc closed numeric / heater (no fixture path)

**Commands:**

- `python -m ai_lab run projects/adhoc_heater_20L_audit --provider mock`
- `python -m ai_lab run projects/adhoc_closed_numeric_prc --provider mock`

| Field | heater audit | closed numeric rod |
|-------|--------------|--------------------|
| run_id | `run_39482acc5410` | `run_d18e928f69a2` |
| final_state | COMPLETED | COMPLETED |
| engineering_outcome | **INSUFFICIENT_EVIDENCE** | **INSUFFICIENT_EVIDENCE** |
| contract | LOCKED / CLOSED_NUMERIC | LOCKED / CLOSED_NUMERIC |
| method | fiber mock → DOMAIN_MISMATCH | fiber mock → METHOD_MISMATCH |
| context on disk | **full** | **full** |
| **verdict** | 🟢 honest non-PASS + full identity |

---

# Regression matrix

| Scenario | After PR-A/B | After PR-C |
|----------|--------------|------------|
| simple_heater | PASS / heater method | PASS + **full disk identity** ✅ |
| ambiguous_rod (1st) | AWAITING_HUMAN / OPEN_ENDED | unchanged ✅ |
| rod + axial resume | AWAITING_HUMAN / DESIGN Required | unchanged ✅ |
| shaft_design | INSUFFICIENT / fiber blocked | unchanged + identity ✅ |
| spider_silk_review | INSUFFICIENT | unchanged + identity ✅ |
| ad-hoc heater | INSUFFICIENT / DOMAIN_MISMATCH | unchanged + identity ✅ |
| incomplete write | soft-fill / empty fields on disk | **MISSING_EXECUTION_CONTEXT** ✅ |

---

# Findings

## FIXED

- **P0-3 (PR-C):** новые `ComputationArtifact` / Claim / EvidenceStore /
  CalculationSpec writes не могут сохраниться только с `run_id` или с
  пустыми `project_id` / `investigation_id` / `task_id`.
- Soft auto-fill на EvidenceStore / KnowledgeService / JsonKnowledge
  убран с runtime write path.
- Legacy read + explicit `legacy_migrate=True` / migration function
  сохранены без ослабления новых writes.
- Physical disk audit подтверждает полную identity.

## STILL FAILING (out of PR-C)

- Нет валидного mechanical-shaft mock oracle → shaft остаётся честным
  non-PASS (не «исправленный PASS»).
- Mock default non-heater всё ещё предлагает fiber (контент) — PASS закрыт
  gate’ами, генерация чужого метода остаётся.

## NEW REGRESSIONS

- None на heater / silk / PR-A rod / PR-B shaft semantics.
- Некоторые unit-фикстуры обновлены: Claim/Artifact конструкторы теперь
  обязаны нести полный контекст (адаптация к новому API, не ослабление
  assertions).

## DEFERRED FINDINGS

| ID | Severity | Item |
|----|----------|------|
| P0-4 / mock oracle | P0/P1 | Корректный mechanical-shaft mock fixture (не fiber) |
| P1-1 | P1 | Claims остаются PROPOSED (claim promotion) |
| P1-3 | P1 | Empty required_outputs → null coverage looks “complete” |
| P2-4 | P2 | Windows console Cyrillic garble |
| P2-adhoc-scope | P2 | Ad-hoc heater wording без явной duration может уйти в HITL (`duration`) — не баг PR-C |

---

# PASS/FAIL decision

**PASS** — цель PR-C достигнута:

- новый write без полного ExecutionContext → hard
  `MISSING_EXECUTION_CONTEXT`;
- legacy только explicit migration/read;
- adversarial unit + E2E + **disk** подтверждают;
- PR-A multi-stage scope и PR-B MethodCompatibilityGate не ослаблены.

---

# Engineering safety verdict

**Может ли система после этого изменения выдать инженерно ложный PASS?**

**NO** для сценария «PASS из-за пустого/частичного execution context» —
такой write теперь падает.

Остаточный риск ложного PASS по **другим** deferred путям (claim
promotion, mock oracle, empty coverage) **не закрыт этим PR**, но и не
ухудшен.

---

# Next recommendation

**Ровно один следующий PR:** валидный **mechanical-shaft mock oracle**
(не fiber), чтобы `shaft_design` мог честно дойти до корректного
расчёта без domain contamination — без ослабления MethodCompatibilityGate
и без claim promotion.
