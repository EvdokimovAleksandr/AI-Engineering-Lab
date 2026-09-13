# AUDIT REPORT — PR-B Method/Domain Compatibility Gate

**Branch:** `feat/lab-modernization-p0-p7`  
**Date:** 2026-09-13  
**Mode:** MOCK (`--provider mock`)  
**Scope:** PR-B only (Method/Domain Compatibility Gate).  
Hard context-on-write и mock-domain oracle для shaft **не** реализованы здесь.

---

# Implementation

Добавлен детерминированный **MethodCompatibilityGate**
(`src/ai_lab/checks/method_compatibility.py`):

- канонические домены задачи: `mechanical_shaft`, `mechanical_rod`,
  `thermal_heating`, `biomaterials_fiber`, `general`;
- семейства методов: `fiber_stress`, `heater_power`, `shaft_mechanics`,
  `rod_stress` (по `objective` + required inputs/outputs, не LLM);
- явная матрица allowed methods; запрет fiber на shaft/rod;
  stress/fiber на heater → `DOMAIN_MISMATCH` / `METHOD_MISMATCH`.

Точки проводки (минимальный diff):

1. **SimulationAgent** — до sandbox: несовместимый CalculationSpec
   сохраняется как `incompatible_*.json` и **снимается** с контракта
   (не может считаться валидным binding).
2. **`evaluate_evidence_completeness`** — `method_compatible` входит в
   `is_complete`; FAIL → completeness incomplete.
3. **Runtime adjudication path** — читает `incompatible_*` с диска и
   принудительно ставит `method_compatible=False` (даже если spec уже
   снят), чтобы `engineering_outcome` не мог стать PASS.

`engineering_outcome=PASS` при domain-wrong calculation **невозможен**
по обоим путям (pre-exec + completeness/adjudication).

---

# Files changed

| File | Change |
|------|--------|
| `src/ai_lab/checks/method_compatibility.py` | **new** — gate + frame + classify |
| `src/ai_lab/checks/calculation_contract.py` | method gate в completeness |
| `src/ai_lab/core/models.py` | `EvidenceCompletenessReport.method_compatible` |
| `src/ai_lab/agents/simulation.py` | pre-exec MethodCompatibilityGate |
| `src/ai_lab/orchestrator/runtime.py` | frame в completeness; `incompatible_*` blocks PASS; contract в `ctx.extra` |
| `tests/test_method_compatibility_pr_b.py` | **new** — adversarial unit/integration |
| `docs/audit-pr-b-method-domain.md` | этот отчёт |

---

# Tests

```text
.venv/Scripts/python.exe -m pytest \
  tests/test_method_compatibility_pr_b.py \
  tests/test_adversarial_integrity.py \
  tests/test_benchmark_integrity.py \
  tests/test_scope_resolver_pr04.py \
  tests/test_context_isolation.py \
  tests/test_v28_investigation_scoping.py -q
# 79 passed
```

Имена (точные) в `test_method_compatibility_pr_b.py`:

- `test_shaft_plus_fiber_calculation_fails`
- `test_rod_plus_fiber_calculation_fails`
- `test_heater_plus_stress_calculation_fails`
- `test_valid_shaft_calculation_allowed`
- `test_valid_heater_calculation_allowed`
- `test_biomaterials_fiber_method_allowed`
- `test_completeness_and_adjudication_block_pass_on_domain_mismatch`
- `test_heater_fixture_path_still_method_compatible`
- (+ domain inference helpers)

---

# Adversarial E2E audit

## 1) simple_heater

**Command:** `python -m ai_lab benchmark run simple_heater --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_4f5ec3bcd590` (audit: also `run_47bc69ec3979`) |
| final_state | COMPLETED |
| engineering_outcome | **PASS** |
| contract status | LOCKED |
| problem_kind | CLOSED_NUMERIC |
| required fields | `[]` |
| calculation method | `calculate_heater_power` |
| domain | thermal / thermal_heating |
| claims support_status | PROPOSED |
| evidence completeness | `method_compatible=True`, `coverage_ratio=1.0` |
| context identity | computation: project/investigation/task still often empty (DEFERRED) |
| **verdict** | 🟢 formal+semantic OK (fixture path); gate allows heater method |

## 2) ambiguous_rod_strength (initial)

**Command:** `python -m ai_lab benchmark run ambiguous_rod_strength --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_af7acf4c3a1f` |
| final_state | AWAITING_HUMAN |
| engineering_outcome | null |
| contract status | NEEDS_CLARIFICATION |
| problem_kind | OPEN_ENDED → (after later resume) DESIGN |
| required fields | `[load_type]` on first stop |
| calculation | none |
| **verdict** | 🟢 first gate OK (PR-A intact) |

## 3) ambiguous_rod + axial resume (PR-A path)

**Command (programmatic):** resume same run with `HitlDecision(choice=axial)`,
`auto_approve=False` (no second-stage auto-answer).

| Field | Value |
|-------|--------|
| run_id | `run_af7acf4c3a1f` |
| final_state | **AWAITING_HUMAN** |
| engineering_outcome | null |
| contract status | **NEEDS_CLARIFICATION** |
| problem_kind | **DESIGN** |
| required fields | `[strength_metric, geometry, design_constraint]` |
| known | `{load_type: axial}` |
| calculation | **none** |
| **verdict** | 🟢 PR-A retained; domain-safe (no false PASS) |

## 4) shaft_design

**Command:** `python -m ai_lab benchmark run shaft_design --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_6b9e64385c59` |
| final_state | COMPLETED |
| engineering_outcome | **INSUFFICIENT_EVIDENCE** (was PASS) |
| contract status | LOCKED |
| problem_kind | DESIGN |
| required fields | `[]` |
| produced calculation method | mock proposed `calculate_fiber_stress` → **rejected** |
| domain (inferred) | `mechanical_shaft` |
| gate codes | `METHOD_MISMATCH`, `OBJECTIVE_MISMATCH`, `INPUT_MISMATCH`, `OUTPUT_MISMATCH` |
| claims support_status | PROPOSED (narrative noise; not PASS) |
| evidence completeness | **`method_compatible=False`** |
| context identity | still incomplete on comps (DEFERRED) |
| **verdict** | 🟢 **PR-B FIXED** — no engineering PASS on fiber; 🟡 no honest shaft oracle yet (DEFERRED) |

## 5) spider_silk_review

**Command:** `python -m ai_lab benchmark run spider_silk_review --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_eb5d891d64d4` |
| final_state | COMPLETED |
| engineering_outcome | INSUFFICIENT_EVIDENCE |
| contract status | LOCKED |
| problem_kind | RESEARCH_REVIEW |
| calculation method | `calculate_fiber_stress` (**allowed** for biomaterials) |
| domain | biomaterials |
| method_compatible | True |
| **verdict** | 🟢 RESEARCH honesty OK; fiber method not blocked on silk |

## 6) ad-hoc closed numeric heater (no fixture)

**Command:** `python -m ai_lab run projects/adhoc_heater_20L_audit --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_53509bb27cb8` |
| final_state | COMPLETED |
| engineering_outcome | **INSUFFICIENT_EVIDENCE** |
| contract status | LOCKED / CLOSED_NUMERIC |
| gate | fiber mock → **DOMAIN_MISMATCH** (thermal vs fiber) |
| missing outputs | `power` |
| **verdict** | 🟢 honest non-PASS; domain contamination blocked from PASS |

---

# Regression matrix

| Scenario | Before PR-B | After PR-B |
|----------|-------------|------------|
| simple_heater | COMPLETED / PASS / heater method | unchanged ✅ |
| ambiguous_rod_strength (1st) | AWAITING_HUMAN / OPEN_ENDED | unchanged ✅ |
| ambiguous_rod + axial resume | AWAITING_HUMAN / DESIGN Required (PR-A) | unchanged ✅ |
| shaft_design | COMPLETED / **PASS** / fiber | COMPLETED / **INSUFFICIENT_EVIDENCE** / gate FAIL ✅ |
| spider_silk_review | INSUFFICIENT_EVIDENCE | unchanged ✅ (fiber allowed) |
| ad-hoc closed numeric heater | INSUFFICIENT / fiber contamination | INSUFFICIENT + **explicit DOMAIN_MISMATCH** ✅ |

---

# Findings

## FIXED

- **P0-2 (PR-B):** domain-wrong CalculationSpec (`calculate_fiber_stress` на
  shaft / rod / heater) больше **не** может привести к
  `engineering_outcome=PASS`.
- Gate детерминированный; срабатывает до exec (снятие контракта) и до
  adjudication (`method_compatible` ∈ `is_complete` + disk `incompatible_*`).

## STILL FAILING (out of PR-B)

- Нет богатого **валидного** mechanical-shaft mock oracle → shaft остаётся
  честным non-PASS, не «исправленным PASS».
- Mock default non-heater всё ещё предлагает fiber (контент) — PASS закрыт,
  но генерация чужого метода остаётся (honesty via refuse, не via correct solve).

## NEW REGRESSIONS

- None on heater / silk / PR-A rod axial path.
- `shaft_design` registry `expected_behavior=PASS` теперь расходится с
  engineering INSUFFICIENT — **намеренно не меняли evaluator** (правило 11).

## DEFERRED FINDINGS

| ID | Severity | Item |
|----|----------|------|
| P0-3 | P0 | Hard context on new `ComputationArtifact` writes (project/investigation/task) |
| P0-4 / mock oracle | P0/P1 | Корректный mechanical-shaft mock fixture (не fiber); не строили здесь |
| P1-1 | P1 | Claims остаются PROPOSED |
| P1-3 | P1 | Empty required_outputs → null coverage looks “complete” |
| P2-4 | P2 | Windows console Cyrillic garble |

---

# PASS/FAIL decision

**PASS** — цель PR-B достигнута:

- невозможно получить engineering PASS, когда расчёт формально к task,
  но семантически решает другую задачу (fiber ≠ shaft/rod/heater);
- adversarial unit + E2E подтверждают;
- PR-A multi-stage scope не ослаблен;
- false pretty PASS на shaft заменён честным INSUFFICIENT_EVIDENCE.

---

# Engineering safety verdict

**Может ли система после этого изменения выдать инженерно ложный PASS?**

**NO** — по сценарию domain-wrong calculation (shaft/rod/heater + fiber/stress).

Воспроизводимый контроль:

```bash
python -m ai_lab benchmark run shaft_design --provider mock
# → COMPLETED, engineering_outcome=INSUFFICIENT_EVIDENCE, method_compatible=False
```

Остаточный риск ложного PASS — другие оси (context IDs, PROPOSED claims,
пустые required_outputs), не method/domain: см. Next recommendation.

---

# Next recommendation

**PR-C — Hard context on write** для новых `ComputationArtifact` /
calculation writes (`project_id` / `investigation_id` / `task_id` обязательны;
legacy soft-read только для миграции). Не реализовывать в этом PR.
