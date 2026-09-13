# AUDIT REPORT — PR-A Multi-step scope after load_type

**Branch:** `feat/lab-modernization-p0-p7`  
**Date:** 2026-09-13  
**Mode:** MOCK (`--provider mock`)  
**Scope:** PR-A only (second-stage Required after OPEN_ENDED → DESIGN). PR-B/PR-C not fixed.

---

# Implementation

После первого scope pass, если `problem_kind` становится `DESIGN` /
`RESEARCH_REVIEW` / `EXPERIMENTAL` / `PARAMETRIC`, выполняется **второй этап**
валидации контракта (`apply_specialized_second_stage`):

- пересчитываются blocking `required_fields` для kind + domain + objective;
- оставшиеся unknown **не** переводятся в optional молча;
- `READY` / `LOCKED` / pipeline только при заполненных Required (или явном
  assumption HITL / доказанной ненужности);
- для open-ended «сделать стержень прочнее» после `load_type=axial`:
  `problem_kind=DESIGN`, но Required =
  `[strength_metric, geometry, design_constraint]` → `NEEDS_CLARIFICATION`.

Точка входа: `finalize_after_clarification` (OPEN_ENDED → DESIGN) +
`finalize_scope_frame` (идемпотентный second stage). ClarificationQuestion /
ClarificationRecord получили `field` для корректного resume.

---

# Files changed

| File | Change |
|------|--------|
| `src/ai_lab/core/investigation.py` | `field` на ClarificationQuestion / ClarificationRecord |
| `src/ai_lab/orchestrator/scope_resolver.py` | second-stage Required API |
| `src/ai_lab/orchestrator/scope.py` | HITL field binding; finalize не чистит Required после axial |
| `tests/test_scope_resolver_pr04.py` | 4 регрессионных теста PR-A |
| `benchmarks/ambiguous_rod_strength/problem.md` | уточнение: axial ≠ READY |
| `docs/audit-pr-a-scope.md` | этот отчёт |

---

# Tests

```text
.venv/Scripts/python.exe -m pytest tests/test_scope_resolver_pr04.py \
  tests/test_scope_resolution.py tests/test_v28_investigation_scoping.py \
  tests/test_benchmark_pr07.py -q
# 37 passed
```

Имена (точные):

- `test_design_requires_second_stage_scope`
- `test_axial_answer_does_not_complete_design_contract`
- `test_required_fields_are_not_cleared_on_resume`
- `test_open_ended_design_cannot_reach_engineering_pass_after_one_answer`

---

# Adversarial E2E audit

## 1) simple_heater

**Command:**
`python -m ai_lab benchmark run simple_heater --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_ae6991417c56` |
| final_state | COMPLETED |
| engineering_outcome | PASS |
| contract status | LOCKED |
| problem_kind | CLOSED_NUMERIC |
| required fields | `[]` |
| calculation method | `calculate_heater_power` |
| domain | thermal_heating |
| claims support_status | all `PROPOSED` |
| evidence completeness | `coverage_ratio=1.0`, `missing_outputs=[]` |
| context identity | computation: `project_id`/`investigation_id`/`run_id` empty; `task_id=calculation` |
| **verdict** | 🟢 formal+semantic OK for fixture path; 🟡 PROPOSED claims; 🔴 context IDs (DEFERRED PR-B) |

## 2) ambiguous_rod_strength (initial)

**Command:**
`python -m ai_lab benchmark run ambiguous_rod_strength --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_2377816c3b46` (later superseded in workspace by axial-only `run_2c548d59f176`) |
| final_state | AWAITING_HUMAN |
| engineering_outcome | null |
| contract status | NEEDS_CLARIFICATION |
| problem_kind | OPEN_ENDED |
| required fields | `[load_type]` |
| calculation | none |
| **verdict** | 🟢 first gate correct |

## 3a) ambiguous_rod + axial-only resume (canonical PR-A)

**Command (programmatic / equivalent CLI intent):**
1. `benchmark run ambiguous_rod_strength --provider mock`
2. Resume same project with **one** pending HITL `choice=axial`, `auto_approve=False`
   so second-stage HITL pauses (does not auto-answer `strength_metric`).

Equivalent for operators:
```bash
python -m ai_lab benchmark run ambiguous_rod_strength --provider mock
# then resume with a single axial answer (UI resume or HitlDecision),
# without --auto-approve-hitl if you need to stop after first answer only
```

| Field | Value |
|-------|--------|
| run_id | `run_2c548d59f176` |
| final_state | **AWAITING_HUMAN** |
| engineering_outcome | null |
| contract status | **NEEDS_CLARIFICATION** |
| problem_kind | **DESIGN** |
| required fields | `[strength_metric, geometry, design_constraint]` |
| known | `{load_type: axial}` |
| calculation | **none** (0 computations) |
| claims | none |
| evidence completeness | n/a (paused) |
| context identity | n/a |
| **verdict** | 🟢 **PR-A FIXED** — axial ≠ LOCKED/PASS; second-stage Required intact |

## 3b) ambiguous_rod + `--resume --auto-approve-hitl` (audit CLI path)

**Command:**
```bash
python -m ai_lab run benchmarks/.workspace/ambiguous_rod_strength \
  --resume --auto-approve-hitl --provider mock
```

Auto-approve continues into second-stage (`strength_metric=yield_strength`), then
clarification budget (2) exhausts → `SCOPE_UNRESOLVED`.

| Field | Value |
|-------|--------|
| run_id | `run_2377816c3b46` |
| final_state | COMPLETED |
| engineering_outcome | **INSUFFICIENT_EVIDENCE** (not PASS) |
| contract status | DRAFT (from UNRESOLVED) |
| problem_kind | DESIGN |
| required fields | `[geometry, design_constraint]` still set |
| known | `load_type=axial`, `strength_metric=yield_strength` |
| calculation | none |
| **verdict** | 🟢 no false engineering PASS; 🟡 budget truncates further HITL (honest UNRESOLVED) |

## 4) shaft_design

**Command:**
`python -m ai_lab benchmark run shaft_design --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_bcf5bce49eb0` |
| final_state | COMPLETED |
| engineering_outcome | **PASS** |
| contract status | LOCKED |
| problem_kind | DESIGN |
| required fields | `[]` |
| calculation method | **`calculate_fiber_stress`** |
| domain | mechanics (content: fiber/spider) |
| claims support_status | all `PROPOSED` |
| evidence completeness | `coverage_ratio=null`, `missing_outputs=[]` |
| context identity | missing project/investigation/task on computation |
| **verdict** | 🔴 **FORMAL PASS / SEMANTIC FAIL** (wrong domain) — **DEFERRED PR-C** |

## 5) spider_silk_review

**Command:**
`python -m ai_lab benchmark run spider_silk_review --provider mock`

| Field | Value |
|-------|--------|
| run_id | `run_5136fbcb9e04` |
| final_state | COMPLETED |
| engineering_outcome | INSUFFICIENT_EVIDENCE |
| contract status | LOCKED |
| problem_kind | RESEARCH_REVIEW |
| required fields | `[]` |
| calculation method | `calculate_fiber_stress` (still scheduled) |
| claims support_status | PROPOSED (+ STUB path) |
| evidence completeness | empty required_outputs → null coverage |
| **verdict** | 🟢 RESEARCH honesty OK; 🟡 calc under mock — DEFERRED PR-C/P2 |

## 6) ad-hoc closed numeric rod

**Command:**
```bash
python -m ai_lab run projects/adhoc_closed_numeric_audit --provider mock
```

| Field | Value |
|-------|--------|
| run_id | `run_3f1d659d4ef1` |
| final_state | COMPLETED |
| engineering_outcome | INSUFFICIENT_EVIDENCE |
| problem_kind | CLOSED_NUMERIC (scope) |
| calculation | mock fiber path; missing `stress_ratio` → irrelevant |
| **verdict** | 🟢 honest fail without fixture; 🔴 domain contamination — DEFERRED PR-C |

---

# Regression matrix

| Scenario | Before PR-A | After PR-A |
|----------|-------------|------------|
| simple_heater | COMPLETED / PASS / LOCKED CLOSED_NUMERIC | unchanged ✅ |
| ambiguous_rod_strength (1st) | AWAITING_HUMAN / OPEN_ENDED / load_type | unchanged ✅ |
| ambiguous_rod + axial resume | **COMPLETED / PASS / LOCKED DESIGN / required=[]** | **AWAITING_HUMAN / NEEDS_CLARIFICATION / DESIGN / non-empty Required** (or UNRESOLVED+INSUFFICIENT if auto-loop) ✅ |
| shaft_design | COMPLETED / PASS / wrong fiber domain | unchanged (still semantic FAIL) — out of PR-A |
| spider_silk_review | COMPLETED / INSUFFICIENT_EVIDENCE | unchanged ✅ |
| ad-hoc closed numeric | INSUFFICIENT / wrong-domain mock | unchanged — out of PR-A |

---

# Findings

## FIXED

- **P0-1 (PR-A):** OPEN_ENDED → one HITL `load_type=axial` → DESIGN READY/LOCKED/PASS.
  Now second-stage Required blocks READY; pipeline does not run as final solution.

## STILL FAILING (out of PR-A)

- **P0-2 / P0-4 / P0-5:** shaft / mock default fiber PASS (PR-C).
- **P0-3:** computation context IDs missing on write (PR-B).
- **P1-1:** claims stay PROPOSED.
- **P1-3:** empty required_outputs → null coverage looks “complete”.

## NEW REGRESSIONS

- None observed on heater / silk / shaft formal paths.
- Note: `--resume --auto-approve-hitl` now may auto-answer the *next* DESIGN
  Required and then hit clarification budget → UNRESOLVED (honest, not PASS).
  Axial-only pause requires not auto-approving the second-stage question.

## DEFERRED FINDINGS

| ID | Severity | Item |
|----|----------|------|
| PR-B / P0-3 | P0 | Hard context on new `ComputationArtifact` writes |
| PR-C / P0-2,P0-4 | P0 | Mock domain honesty (no fiber PASS for shaft/arbitrary) |
| P1-1 | P1 | Claim support promotion |
| P1-3 / PR-E | P1 | Coverage semantics for empty required_outputs |
| P2 | P2 | DESIGN open-strengthen needs >2 clarification rounds or multi-field HITL to fully READY |
| P2-4 | P2 | Windows console Cyrillic garble in logs |

---

# PASS/FAIL decision

**PASS** — проблема PR-A реально исправлена:

- после `load_type=axial` контракт **не** LOCKED;
- engineering **не** PASS;
- Required DESIGN non-empty / `NEEDS_CLARIFICATION`;
- engineering calculation не является финальным решением.

(Сопутствующие P0 mock-domain / context — не регрессии PR-A, а DEFERRED.)

---

# Engineering safety verdict

**Может ли система после этого изменения выдать инженерно ложный PASS?**

**YES** — но **не** по сценарию PR-A (open rod + axial).

Воспроизводимый сценарий вне PR-A:

```bash
python -m ai_lab benchmark run shaft_design --provider mock
# → COMPLETED, engineering_outcome=PASS, method=calculate_fiber_stress
```

Open-ended rod after axial alone: **NO** false PASS (fixed).

---

# Next recommendation

**PR-B hard context on write**
