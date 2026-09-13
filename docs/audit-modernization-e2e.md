# Adversarial E2E audit — lab modernization (P0–P7)

**Branch:** `feat/lab-modernization-p0-p7`  
**Date:** 2026-09-13  
**Mode of runs:** MOCK (`--provider mock`)  
**Scope:** validation of the lab *as a system* (semantics), not a new feature PR.

Scenarios (`python -m ai_lab benchmark scenario --all`): **6/6 passed** (formal).  
Focused pytest (`test_context_isolation`, `test_v28_investigation_scoping`): **14 passed**.  
Primary evidence below is from **real run artifacts**, not green pytest counts.

Temporary ad-hoc project left untracked: `projects/adhoc_heater_20L_audit/` (no git commit).

---

## 1. Executive verdict

**Blocked for “lab validation” use in live UI / non-fixture MOCK paths.**

Modernization correctly implements several *formal* gates (HITL stop on open-ended rod, units length≠litre scenario, RESEARCH_EMPTY ≠ PASS, context *mismatch* when IDs disagree).  
But the system still produces **formal PASS / COMPLETED / LOCKED** outcomes that are **engineering-false**:

1. After HITL `load_type=axial`, open-ended rod is treated as fully defined → **READY/LOCKED → calculation → PASS**.
2. Under mock without domain fixtures, **shaft / design** problems are solved as **spider-silk fiber stress** and still get `engineering_outcome=PASS`.
3. **New** `ComputationArtifact` writes still omit `project_id` / `investigation_id` / `task_id` (only `run_id`); empty context is still accepted via legacy soft-match.
4. UI/eval can look “healthy” (`COMPLETED` + category PASS) while claims stay `PROPOSED` and domain content is wrong.

**Trust today:** orchestration shapes, HITL *pause* on first ambiguous gate, RESEARCH honesty (`INSUFFICIENT_EVIDENCE` + `RESEARCH_EMPTY`), unit-dimension scenario, ID *mismatch* isolation tests.  
**Do not trust:** MOCK `PASS` without a domain fixture, post-clarification “design is ready”, live closed numeric without fixture/`cursor_sdk`, synthesis/UI numbers from `PROPOSED` claims, completeness of context stamping on computations.

---

## 2. Per-benchmark / ad-hoc table

| Case | Command | run_id | MODE | final_state | engineering_outcome | Contract | Semantic flags |
|------|---------|--------|------|-------------|---------------------|----------|----------------|
| **simple_heater** | `benchmark run simple_heater --provider mock` | `run_bb54e7eab5e8` | MOCK (CLI); manifest has `model_provider=mock`, no `MODE` field | COMPLETED | PASS | LOCKED / CLOSED_NUMERIC / Known volume…losses / Required=[] | 🟢 fixture-bound power path; 🟡 all claims `PROPOSED`; 🔴 comps missing project/investigation/task_id; TaskGraph CV only in `metadata` |
| **ambiguous_rod** (1st) | `benchmark run ambiguous_rod_strength --provider mock` | `run_1e58a82c3680` | MOCK | AWAITING_HUMAN | null | NEEDS_CLARIFICATION / OPEN_ENDED / Required=`[load_type]` | 🟢 first gate correct |
| **ambiguous_rod** (resume axial) | `run …/ambiguous_rod_strength --resume --auto-approve-hitl` | same | MOCK | COMPLETED | **PASS** | **LOCKED / DESIGN** / Required=[] / Known=`load_type` only; `strength_metric` still unknown | 🔴 multi-step scope fail; 🔴 fiber/spider claims; 🔴 `calculate_fiber_stress`; eval `expected_behavior=FAIL` (“open-ended produced PASS”) |
| **shaft_design** | `benchmark run shaft_design --provider mock` | `run_cf5014d9263d` | MOCK | COMPLETED | **PASS** | LOCKED / DESIGN | 🔴 wrong domain (fiber stress / spider claims) vs shaft diameter problem; 🟢 routing STANDARD; eval overall PARTIAL but engineering_success PASS |
| **spider_silk_review** | `benchmark run spider_silk_review --provider mock` | `run_ae88f5f2e381` | MOCK | COMPLETED | INSUFFICIENT_EVIDENCE | LOCKED / RESEARCH_REVIEW / required_evidence_kinds FACT | 🟢 RESEARCH_EMPTY honesty; 🟡 still ran calculation; 🟡 iteration_progress `no_progress=false` with empty known/learned; STUB claim present |
| **adhoc heater 20L** | `run projects/adhoc_heater_20L_audit --provider mock` | `run_5dfd840277e6` | MOCK | COMPLETED | INSUFFICIENT_EVIDENCE | LOCKED / CLOSED_NUMERIC / outs power | 🟢 honest fail without fixture; 🔴 cspec merged `power`+`stress_gpa` + fiber code; 🔴 comp missing context IDs |

**Scenario suite (not full pipeline):** all 6 formal expectations met (`context_isolation`, `units`, `scope_resolution`, `ambiguous_engineering`, `budget_control`, `research_review`).

---

## 3. Deep dive — three user concerns

### 3.1 Multi-step scope gate (HITL `load_type=axial`)

**Observed**

1. Initial run correctly stops: `SCOPE_NEEDS_CLARIFICATION`, contract `NEEDS_CLARIFICATION`, Required=`load_type`, `AWAITING_HUMAN`.
2. Resume with choice `axial` (auto-approve picks `options[0]`):
   - `finalize_after_clarification` (`src/ai_lab/orchestrator/scope.py`) sets:
     - `problem_kind=DESIGN`
     - `status=SCOPE_RESOLVED`
     - **`required_fields=[]`** (clears the only Required)
     - does **not** promote remaining unknowns (`strength_metric`) or DESIGN-specific Required (geometry, load magnitude, material allowables, success metric)
   - Contract becomes **READY → LOCKED** with empty `required`, objective “Propose how to strengthen the rod under axial loading”
   - Pipeline runs STANDARD calculation; mock emits fiber stress; adjudication **PASS**

**Verdict:** User concern **confirmed (P0)**. One clarification is treated as “defined enough”. Optional fields stay optional (correct), but **no second Required frame** for the chosen investigation type (DESIGN under axial). `unknown=['strength_metric']` survives without blocking READY.

**Pointers**

- `src/ai_lab/orchestrator/scope.py` — `finalize_after_clarification` (~650–676)
- `src/ai_lab/orchestrator/scope_resolver.py` — `_open_rod_stronger_scope` (Required only `load_type`)
- Artifacts: `benchmarks/.workspace/ambiguous_rod_strength/.runs/run_1e58a82c3680/planner/{scope,engineering_contract}.json`

### 3.2 Context mandatory for new artifacts

**Observed**

| Layer | Behavior |
|-------|----------|
| `require_context_match` | Empty/None actual fields **allowed** (“legacy on-disk”) — wrong non-empty still fails |
| `Claim` / `ComputationArtifact` models | `project_id` / `investigation_id` / `task_id` still **Optional** |
| `EvidenceStore.save_claim` | Soft-fills empty `project_id` / `investigation_id` / `run_id` — does not hard-require `task_id` |
| `CalculationSpec` proposal | Stamps when `ExecutionContext` present; legacy fill-if-unset path remains |
| `sandbox/local.py` → `ComputationArtifact` | Sets **`run_id` only**; `task_id` only in `metadata`; **no** `project_id` / `investigation_id` |
| `RunStore.save_computation` | Writes JSON as-is — **no stamp / no refuse** |
| Lineage verifier | Compares IDs only when **both** sides set — empty computation IDs do not fail |

**All five runs** produced computations missing `project_id`, `investigation_id`, `task_id`.

**Verdict:** User concern **confirmed (P0)**. Legacy soft-read path is not isolated to a migration layer; **new writes** can and do omit identity fields. Semantic “wrong domain, right project_id on claims” still passes ID isolation (shaft/rod stamped with local project_id but spider/fiber *content*).

**Pointers**

- `src/ai_lab/core/execution_context.py` — `require_context_match` legacy empty skip
- `src/ai_lab/sandbox/local.py` — artifact construction (~171+)
- `src/ai_lab/memory/run_store.py` — `save_computation`
- Example: `projects/adhoc_heater_20L_audit/.runs/run_5dfd840277e6/computations/comp_33401f72df1a.json`

### 3.3 Real E2E suite (not only pytest)

Executed end-to-end under MOCK:

1. `simple_heater` — PASS only because registry injects `simulation_fixture=heater_correct`.
2. `ambiguous_rod_strength` — HITL OK; resume axial → semantic FAIL (see 3.1).
3. `shaft_design` — formal PASS / wrong physics content.
4. `spider_silk_review` — orchestration OK; engineering INSUFFICIENT_EVIDENCE / RESEARCH_EMPTY (honest).
5. Ad-hoc closed heater (20 L, 20→80°C, 30 min, 15% losses) — **without fixture** → INSUFFICIENT_EVIDENCE (missing `power`), cspec contaminated with fiber outputs.

**Implication:** Benchmark “PASS” under mock is often **fixture theatre**, not proof that a user problem would solve correctly. Live UI creates runs **without** `simulation_fixture` (intentionally blocked from UI JSON) → closed heater-like tasks behave like ad-hoc (fail or wrong-domain), while shaft-like tasks can still **PASS on fiber** because mock defaults non-heater → `stress_gpa`.

---

## 4. Semantic violations (formal-pass / semantic-fail)

### P0 — must fix before lab validation

| ID | Violation | Evidence |
|----|-----------|----------|
| **P0-1** | OPEN_ENDED → one HITL answer → DESIGN READY/LOCKED/PASS without further Required | Resume axial; `finalize_after_clarification`; eval category `expected_behavior=FAIL` |
| **P0-2** | Wrong-domain calculation attachable & PASS under mock (shaft/rod → fiber stress) | `cspec_*.json` `objective=calculate_fiber_stress`; claims about spider silk on `shaft_design` / `ambiguous_rod_strength` |
| **P0-3** | New `ComputationArtifact` writes omit project/investigation/task context | Every run’s `computations/comp_*.json`; sandbox constructor |
| **P0-4** | Mock default non-heater = silk/fiber; heater PASS requires hidden fixture | `llm/mock.py` chief/sim branches; `benchmark/registry.py` `simulation_fixture=heater_correct`; ad-hoc fails without it |
| **P0-5** | Evaluator/UI can score engineering_success PASS on domain-wrong STANDARD runs | `evaluate_run(shaft_design)` → engineering_success PASS |

### P1 — high risk / false confidence

| ID | Violation | Evidence |
|----|-----------|----------|
| **P1-1** | Claims remain `PROPOSED`; synthesis still accepts PROPOSED when computation lineage + verified check exist | `orchestrator/synthesis.py`; heater/shaft `accepted_claims[].support_status=PROPOSED` |
| **P1-2** | `COMPLETED` + `engineering_outcome` null/PASS conflation in mental model; MODE only on CLI print, not manifest field | Manifests use `model_provider`, not `MODE` |
| **P1-3** | Empty required_outputs → `coverage_ratio=null` treated as complete (`missing_outputs=[]`) for design/research | rod/shaft/spider evidence_completeness |
| **P1-4** | CalculationSpec can list conflicting outputs (`power` + `stress_gpa`) | ad-hoc cspec |
| **P1-5** | Iteration progress marks `no_progress=false` with empty known/learned/gaps; stop may come from adjudication not IterationController | spider `iteration_progress.json` |

### P2 — hygiene / consistency

| ID | Violation | Evidence |
|----|-----------|----------|
| **P2-1** | Contract `task_id` often null; some claims `task_id=None` | heater/rod contracts & framing claims |
| **P2-2** | TaskGraph top-level `contract_version` null (binding lives in `metadata` — works, but easy to mis-audit) | planner/task_graph.json |
| **P2-3** | RESEARCH run still schedules calculation under mock silk defaults | spider pipeline steps |
| **P2-4** | Windows console encoding can garble Cyrillic in logs/artifacts display | audit tooling note only |

**Checklist hunt (summary)**

| Check | Result |
|-------|--------|
| COMPLETED without engineering PASS as success? | Partial: spider COMPLETED + INSUFFICIENT_EVIDENCE (OK); tech_success still PASS |
| RESEARCH_EMPTY / STUB as FACT/PASS? | Mostly honest (INSUFFICIENT_EVIDENCE); STUB claim stored as INFERENCE not FACT |
| OPEN_ENDED READY after one clarification? | **Yes — fail** |
| Wrong-domain CalculationSpec attachable? | **Yes under mock** |
| Legacy empty context on NEW writes? | **Yes** |
| No-progress loops burn iterations? | Scenario budget_control OK; spider stopped early via adjudication |
| Synthesis accepting PROPOSED? | **Yes if verified computation provenance** |
| Units length vs litre? | Scenario PASS (formal) |
| MODE missing / MOCK as live failure? | MODE CLI-only; mock PASS looks live-success |
| DRAFT/NEEDS_CLARIFICATION allowing calc? | First rod gate blocks; after illegal READY, calc allowed |

---

## 5. Recommended next coding PRs (small, sequential)

Do **not** open a mega “fix everything” PR. Suggested order:

1. **PR-A — Multi-step scope after load_type**  
   After axial/bending/combined: keep `NEEDS_CLARIFICATION` (or DESIGN with non-empty Required) until DESIGN-blocking fields are answered or explicitly assumed via HITL. Stop clearing `required_fields` to `[]` in `finalize_after_clarification`. Add resume E2E asserting *not* LOCKED/PASS after only `load_type`.

2. **PR-B — Hard context on new writes**  
   Stamp `project_id`/`investigation_id`/`task_id`/`run_id` in sandbox + `save_computation`; refuse save if unset. Keep empty-accept **only** behind an explicit `legacy_migrate=True` read path. Extend tests beyond ID mismatch to “missing IDs on write”.

3. **PR-C — Mock domain honesty**  
   Non-heater mock must not default to fiber PASS for arbitrary problems: either fail closed/`INSUFFICIENT_EVIDENCE` without fixture, or require per-benchmark fixtures for shaft (and forbid PASS without fixture in evaluate). Surface `MODE=MOCK` in manifest + UI badge.

4. **PR-D — Claim support promotion**  
   After verified computation, promote to `SUPPORTED`/`WEAKLY_SUPPORTED` or refuse listing PROPOSED in `accepted_claims` quantitative cards.

5. **PR-E — Coverage semantics for empty required_outputs**  
   DESIGN/RESEARCH with no required_outputs must not look “complete”; require evidence_kinds coverage or keep NEEDS_CLARIFICATION / INSUFFICIENT_EVIDENCE.

6. **PR-F — Ad-hoc closed numeric fixture-free path** (optional after C)  
   Deterministic solver or problem-bound mock for heater numbers without registry fixture, so CLI/UI closed tasks aren’t false-negative only when fixture missing and false-positive when wrong domain matches.

---

## 6. What to trust today vs not (live UI)

### Trust

- First-contact HITL on open-ended rod (`load_type`).
- Refusal to call empty research a numeric PASS (`RESEARCH_EMPTY` → INSUFFICIENT_EVIDENCE).
- Unit gate when expected dimension is length and actual is litre (scenario).
- Hard fail when **non-empty** context IDs disagree (sofa vs rod fixture tests).
- Run namespace isolation of claim files under `.runs/<run_id>/`.
- Budget/no-progress scenario fixture (`STOP_INSUFFICIENT_EVIDENCE`).

### Do not trust

- Any MOCK `engineering_outcome=PASS` unless you verified fixture + domain outputs by hand.
- Post-clarification “problem is fully scoped” / LOCKED design contracts after a single Required answer.
- UI “success” from `final_state=COMPLETED` alone.
- Quantitative cards / accepted claims while `support_status=PROPOSED`.
- Computation lineage completeness (missing identity fields).
- Shaft / design results under mock without a shaft-specific fixture.
- Closed heater from UI/CLI without fixture (will not mirror `simple_heater` benchmark PASS).

---

## 7. Artifact index (this audit)

| Run | Path |
|-----|------|
| simple_heater | `benchmarks/.workspace/simple_heater/.runs/run_bb54e7eab5e8/` |
| ambiguous_rod (+ resume) | `benchmarks/.workspace/ambiguous_rod_strength/.runs/run_1e58a82c3680/` |
| shaft_design | `benchmarks/.workspace/shaft_design/.runs/run_cf5014d9263d/` |
| spider_silk_review | `benchmarks/.workspace/spider_silk_review/.runs/run_ae88f5f2e381/` |
| ad-hoc heater | `projects/adhoc_heater_20L_audit/.runs/run_5dfd840277e6/` |

No commits or pushes were made for this audit.
