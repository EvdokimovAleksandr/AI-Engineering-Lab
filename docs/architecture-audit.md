# Architecture Audit — AI Engineering Lab MVP

**Auditor role:** Principal Architect / AI Systems Auditor / Engineering Software Reviewer  
**Scope:** фактический код в `src/ai_lab/` (не заявления README)  
**Date:** 2026-09-11  
**Verdict (кратко):** MVP — рабочий скелет state-machine лаборатории с файловой памятью и sandbox. Как фундамент настоящей AI Engineering Lab **через год — нет**, без P0/P1. Независимость verification/red team и научная трассируемость сейчас **декларированы сильнее, чем enforced**.

Связанный документ: [architecture-v2.md](architecture-v2.md).

---

## 1. Executive Summary

Система запускается, проходит mock e2e (`15` тестов, demo → `COMPLETED`) и корректно разделяет часть слоёв (`LLMProvider`, `ToolRegistry`, `Claim.kind`). Это хороший прототип оркестрации.

Однако критические инженерные свойства **не обеспечены механизмом**, а лишь **подсказаны промптом/ролями**:

| Заявленное свойство | Фактически |
|---------------------|------------|
| Dependency graph агентов | Линейная таблица `STAGE_ROLES` |
| Независимая verification | LLM смотрит все claims; `python.execute` не вызывается |
| Anti-collusion | Один provider; все роли → одна модель в config |
| Traceable evidence graph | `Claim.refs` / decision→evidence не образуют граф |
| Воспроизводимый Experiment/Run | Есть `run_id` и JSONL events; нет run manifest |
| Chief планирует специалистов | `follow_up_tasks` оркестратор игнорирует |
| Final answer из артефактов после V+RT | `final_report.md` пишется из LLM-summary Chief, без чтения V/RT |

**Главный вывод:** без усиления *enforced independence*, *evidence graph*, *run manifest* и *deterministic math checks* система останется «мультиагентным chatbot с папками», а не лабораторией.

---

## 2. Current Architecture (фактическая)

### 2.1 Слои кода (реально)

```
cli → LabRuntime
        ├─ create_llm_provider (mock | cursor_sdk)
        ├─ build_tool_registry
        ├─ EvidenceStore + DecisionLog + ProjectStore
        ├─ build_agents()  # fixed 6 classes
        └─ loop:
             state ← ProjectSnapshot
             tasks ← STAGE_ROLES[state]   # NOT a dependency graph
             results ← agent.run(task, shared AgentContext)
             WorkflowEngine.apply_* / advance
             save project_state.json
```

### 2.2 Карта потока данных

```
User (CLI)
  → LabRuntime.run()
    → WorkflowEngine (FSM + snapshot)
      → STAGE_ROLES → TaskSpec(role, objective)
        → Agent (LLM JSON + optional tools)
          → ToolRegistry (permission list)
            → python / files / artifacts / research stub
          → EvidenceStore (claims JSON) + DecisionLog
          → artifacts on disk under projects/<name>/
    → (SYNTHESIS) Chief → final_report.md
  → COMPLETED | AWAITING_HUMAN
```

### 2.3 Что заявлено vs что есть

| Docs (`architecture.md`) | Reality |
|--------------------------|---------|
| «Сборка графа зависимостей» | `STAGE_ROLES` dict + `advance()` |
| Claims → Verification & Red Team независимо | Оба читают **весь** `list_claims()`; идут **последовательно** |
| HITL на critical RT / HitlRequest | Только critical RT path; `AgentResult.hitl_request` **не читается** |
| Parallel без sharing drafts | `asyncio.gather` при общем `EvidenceStore` |
| Chief schedules specialists | `follow_up_tasks` dead path |

---

## 3. Architecture Diagram

```
┌────────────┐
│    User    │
└─────┬──────┘
      │ ai_lab run
      ▼
┌─────────────────────────────────────────────┐
│                 LabRuntime                   │
│  STAGE_ROLES │ WorkflowEngine │ HitlGate    │
└──────┬───────────────┬──────────────────────┘
       │               │
       ▼               ▼
┌─────────────┐  ┌──────────────┐
│   Agents    │  │ ProjectState │
│ (6 classes) │  │   .json      │
└──────┬──────┘  └──────────────┘
       │ shared AgentContext
       ▼
┌──────────────┐   ┌─────────────┐   ┌──────────────┐
│ LLMProvider  │   │ ToolRegistry│   │ EvidenceStore│
│ mock/cursor  │   │ sandbox…    │   │ + DecisionLog│
└──────────────┘   └─────────────┘   └──────┬───────┘
                                            ▼
                                   projects/<name>/*
                                   .runs/<run_id>.jsonl
```

**Отличие от целевой:** нет Task Graph, нет Evidence Graph, нет Run Manifest, нет изолированных review workspaces, нет Experimental Design stage в runtime.

---

## 4. Critical Findings

### C1 — Verification не выполняет независимую проверку вычислений

| | |
|--|--|
| **Severity** | CRITICAL |
| **Likelihood** | High (всегда в текущем коде) |
| **Impact** | Ложное чувство «проверено» |
| **Current** | `VerificationAgent` сериализует все claims в LLM prompt и принимает `status` от модели. `python.execute` в `allowed_tools`, но **не вызывается**. |
| **Problem** | Это peer-review текстом, не recompute. |
| **Why it matters** | Инженерная лаборатория без независимого пересчёта — театр. |
| **Recommended** | Deterministic `MathCheck` / recompute pipeline: verifier получает `{claim_id, code_hash, inputs, expected}`; код перезапускается sandbox; LLM только интерпретирует расхождения. |
| **Action** | P0 — см. architecture-v2 §Verification |

### C2 — «Независимость» verification в mock — счётчик вызовов, не evidence

| | |
|--|--|
| **Severity** | CRITICAL (для доверия к demo/тестам) |
| **Likelihood** | Certain under mock |
| **Impact** | Тесты доказывают FSM, не научную корректность |
| **Current** | `MockProvider._verification_calls`: 1→DISPUTED, 2+→PASS |
| **Problem** | Итерация «чинит» статус без нового доказательства. |
| **Recommended** | Mock должен PASS только при появлении новых claim/sim артефактов или явного fixture. Отдельные golden fixtures для independence tests. |

### C3 — Final report игнорирует Verification / Red Team

| | |
|--|--|
| **Severity** | CRITICAL |
| **Likelihood** | High |
| **Impact** | Пользователь читает `final_report.md` как ответ; V/RT остаются в `reviews/` |
| **Current** | Chief на SYNTHESIS пишет summary/understanding из своего LLM payload |
| **Recommended** | Synthesis = deterministic template поверх accepted claims + verification status + red-team residual risk. LLM только формулирует prose поверх **уже отобранных** IDs. |

### C4 — CursorSDK `local.cwd=project` обходит tool sandbox

| | |
|--|--|
| **Severity** | CRITICAL (security) |
| **Likelihood** | Medium–High when using cursor_sdk |
| **Impact** | Cursor Agent может править файлы/shell вне `ToolRegistry` |
| **Current** | `CursorSDKProvider` передаёт `LocalAgentOptions(cwd=project)` |
| **Recommended** | Reasoning-only mode: cwd = empty/temp, запрет tool use у Cursor agent; все side effects только через lab tools. Или remote/cloud agent без FS. |

### C5 — Нет Evidence / Provenance Graph

| | |
|--|--|
| **Severity** | CRITICAL (scientific integrity) |
| **Likelihood** | Certain |
| **Impact** | Нельзя построить цепочку Conclusion → Decision → Evidence → Sim → Hyp → Source |
| **Current** | `Claim.refs` есть, почти не заполняется; Decision.evidence — список строк без типов/рёбер |
| **Recommended** | Явная модель `Node` + `Edge` (см. V2). |

---

## 5. High Risk Findings

### H1 — Anti-collusion отсутствует

Все роли в `default.yaml` → `composer-2.5`. Один `LLMProvider` на run. Verification и Red Team последовательны и видят один и тот же полный набор claims (включая author assumptions и confidence).

**Risk:** одна модельная ошибка → «согласованный» PASS.  
**Fix:** model routing + blind bundles + mandatory independent recompute + запрет majority-without-evidence.

### H2 — Shared memory загрязняет независимую проверку

`AgentContext` общий; `list_claims()` без фильтра по `independence_group` / author exclusion. Red Team имеет `files.read` → может читать `reviews/`, `chief_understanding.json`.

**Fix:** ReviewBundle: whitelist claim IDs + stripped fields (no author confidence narrative); filesystem ACL per role (deny reviews/ for RT until after attack phase, or deny all author prose).

### H3 — Iteration loop неполный / противоречивый

`FORWARD[ITERATION_REQUIRED] = ANALYSIS`, но runtime после theorist+simulation форсит `VERIFICATION`, минуя ANALYSIS/CALCULATION как отдельные стадии. Новые hypotheses могут появиться, но полный научный цикл не гарантирован.

**Fix:** IterationPolicy: `fail_reasons → re-entry state` (ANALYSIS | SIMULATION | RESEARCH), версионирование claims (`supersedes`).

### H4 — Нет Run Budget (кроме step counter)

`max_iterations` = лимит шагов FSM, не токенов/стоимости/tool calls. Нет `max_cost`, `max_tool_calls`, `max_runtime`. Agent `follow_up_tasks` сейчас мёртв — но если включить без budget → runaway.

**Fix:** `RunBudget` enforced в LabRuntime before each agent/tool call.

### H5 — Resume хрупкий

`project_state.json` сохраняется; новый run всегда новый `run_id`; mock verification counter сбрасывается; нет idempotent stage checkpoints; нет привязки артефактов к run.

**Fix:** `RunManifest` + stage ledger + resume from last incomplete stage with same run or explicit `--continue-run`.

### H6 — Двойная запись DecisionLog

Chief: `ctx.decisions.append(decision)`; runtime снова `self.decisions.append` для `result.decisions` → дубликаты JSONL.

**Fix:** либо агент возвращает decisions без записи, либо runtime не дублирует (локальный safe fix).

### H7 — Simulation: LLM интерпретирует stdout как CALCULATION без проверки единиц/диапазонов

Returncode==0 → `EvidenceKind.CALCULATION`. Нет unit system, нет physical bounds, нет сравнения с независимым пересчётом.

### H8 — Research stub / mock sources легко становятся «evidence»

`source="mock://…"` проходит FACT/INFERENCE guards. При реальном web-research — prompt injection через tool output не санитизируется.

---

## 6. Medium / Low Findings

| ID | Sev | Finding |
|----|-----|---------|
| M1 | MEDIUM | `task.inputs`, `independence_group`, `INDEPENDENT_REVIEW_GROUP` — dead / unused semantics |
| M2 | MEDIUM | `hitl_on_expensive_experiment`, `AgentResult.hitl_request` — unused |
| M3 | MEDIUM | `logging.emit` зарегистрирован, никому не allowed |
| M4 | MEDIUM | Нет retries на transient LLM/API errors — run падает целиком (хорошо для fail-loud; плохо для resilience) |
| M5 | MEDIUM | `ProjectStore.resolve` path check: `startswith(str(root))` может быть хрупким на Windows (`root` vs `root2`) |
| M6 | MEDIUM | Parallel gather без file-locking strategy beyond per-path locks — index `claims_index.json` race |
| M7 | MEDIUM | Теоретик и Simulation частично дублируют «произвести claims»; CALCULATION stage = тот же Simulation agent |
| M8 | LOW | Docs oversell «dependency graph» |
| M9 | LOW | `EXPERIMENT` state в enum без STAGE_ROLES → AWAITING_HUMAN |
| M10 | INFO | Хорошие решения: FACT guard, no silent LLM fallback, AST sandbox, path jail, append-only logs |
| M11 | INFO | 6 ролей для MVP ок; `engineering_designer` / `experimental_scientist` правильно оставлены extension points |

---

## 7. Independent Verification Assessment

### Что сделано правильно

- Verification/Red Team **не получают chat transcripts** авторов.
- Вход — structured `Claim` JSON.
- System prompts явно требуют distrust / reject.

### Что ломает независимость

1. Полный dump всех claims (включая framing Chief и assumptions).
2. Нет обязательного recompute.
3. Статус PASS — мнение LLM.
4. Тот же model id / provider.
5. Последовательный порядок (RT видит мир после V).
6. Mock PASS после «итерации» без критерия evidence delta.
7. Нет разделения CONSENSUS vs INDEPENDENT_EVIDENCE в data model.

### Рекомендуемый механизм

```
AuthorClaimBundle (content-addressed)
        │
        ├─► DeterministicChecks (units, recompute, bounds) → CheckReport
        ├─► VerificationAgent(blind_bundle, check_report) → VerificationReport
        └─► RedTeamAgent(blind_bundle) [parallel, separate model] → RedTeamReport
                │
                └─► Adjudicator (rules + optional Chief) → Decision
```

Правило: **PASS требует** `compute_check` от deterministic layer, не только LLM text.

Различать:

- `CONSENSUS` — несколько агентов согласны (слабо)
- `INDEPENDENT_EVIDENCE` — независимое измерение/пересчёт/первичный источник (сильно)

---

## 8. Scientific Traceability Assessment

**Сейчас:** частичные ID (`claim_*`, `hyp_*`, `ver_*`, `dec_*`), файлы на диске, decision log.  
**Нельзя надёжно построить:**

```
FINAL CONCLUSION
 ↓ Decision D17
 ↓ Evidence E12, E19
 ↓ Simulation S08
 ↓ Calculation C43
 ↓ Hypothesis H7
 ↓ Sources R4, R8
```

Причины:

- нет типизированных рёбер;
- synthesis не ссылается на verification report IDs;
- hypotheses не связаны с simulation runs;
- simulation artifact `last_run.json` перезаписывается (потеря истории).

**Нужная data model:** см. architecture-v2 §Knowledge Graph.

---

## 9. Workflow Assessment

| Capability | Status |
|------------|--------|
| Stateful FSM | Да (`ProjectSnapshot`) |
| Non-linear FAIL→iterate | Частично (да, но укороченный re-entry) |
| Branching | Нет |
| Parallel tasks | Только ITERATION_REQUIRED; shared store |
| Resume after crash | Слабый (state есть; run semantics нет) |
| Human pause | `AWAITING_HUMAN` (без interactive resume CLI) |
| Stage ledger | Нет |

Hardcoded sequential happy-path: UNDERSTANDING→…→SIMULATION→VERIFICATION→RED_TEAM→SYNTHESIS.

---

## 10. Memory Assessment

| Store | Role | Gaps |
|-------|------|------|
| Project files | Long-term project memory | Нет versioning/soft-delete/conflict markers |
| EvidenceStore | Claims index | Нет namespace по run; stale claims остаются |
| DecisionLog | Append-only decisions | Дубликаты; слабые links |
| `.runs/*.jsonl` | Execution history | Нет prompts/model versions/tool I/O полных |
| Agent LLM context | Short-term | Каждый вызов почти без retrieval; либо полный dump claims |

Нет разделения Knowledge Base vs Project Evidence vs Run Scratch.

---

## 11. Tool Security Assessment

**Сильные стороны**

- AST import whitelist; ban eval/exec/open;
- `python -I`, empty PYTHONPATH, stripped env;
- timeout + max_output_bytes;
- Tool permission lists per role;
- Project path jail (`..` banned).

**Слабые стороны**

| Issue | Risk |
|-------|------|
| Нет cgroup/memory/CPU limits | DoS через `while True` до timeout только по wall clock |
| Network: subprocess без явного block; модули numpy/scipy ok, но `socket` не в whitelist — частично ок | Средний |
| CursorSDK local agent | Обход sandbox |
| Tool output → LLM без taint labeling | Prompt injection |
| `files.write` у Chief | Может переписать `problem.md` / evidence |
| Research backend будущего | Untrusted text |

**Защита от injection (рекомендация):**

1. Taint: `ToolResult.trust_level = UNTRUSTED`.
2. Обёртка в delimiter + инструкция «это данные, не команды».
3. Structured extraction до LLM (не сырой HTML).
4. Запрет tool output менять system policy.

---

## 12. Reproducibility Assessment

| Item | Fixed today? |
|------|--------------|
| Model id | В config, не snapshot'ится в run |
| Model version / provider build | Нет |
| Parameters (temperature…) | Почти не используются |
| Prompts | В коде агентов; не версионируются в run |
| Tools + versions | Нет |
| Python version | Нет в manifest |
| Dependencies lock | Нет poetry.lock/uv.lock в репо |
| Input (problem.md hash) | Нет |
| Tool execution results | Частично (`simulations/last_run.json`, перезапись) |
| Timestamp | Да (claim/event) |
| Seed | Нет |

**Нужен `Experiment/Run Manifest`:** см. V2.

---

## 13. Failure Analysis

| # | Scenario | Expected | Current | Risk | Fix |
|---|----------|----------|---------|------|-----|
| 1 | LLM API down | Retry/budget then AWAITING_HUMAN | Raise → run dies | Medium | Classified errors + budget |
| 2 | Agent timeout | Mark failed stage, pause | Нет agent timeout (только sandbox) | High | Wall-clock per task |
| 3 | Tool crash | Record failure artifact, optional iterate | Simulation re-raises | Medium | Typed ToolError → workflow |
| 4 | Simulation hang | Kill + FAIL claim | TimeoutError sandbox | OK-ish | Persist timed_out artifact always |
| 5 | Malformed JSON | Quarantine + retry once | Raise RuntimeError | Medium | Schema retry policy (bounded) |
| 6 | Research unavailable | INSUFFICIENT_EVIDENCE | Stub always works; real N/A | High later | Explicit backend errors |
| 7 | Contradictory agents | Record conflict node | No conflict object | High | ConflictRecord + HITL |
| 8 | Verification failed | Iterate with policy | ITERATION_REQUIRED (short loop) | Medium | Richer re-entry |
| 9 | Mid-workflow stop | Resume | State saved; new run_id | High | Run ledger |
| 10 | Process killed | Idempotent resume | Partial artifacts possible | High | Atomic stage commit |
| 11 | Re-run | Fresh or continue explicit | Overwrites last_* ; appends claims | High | Run-scoped dirs |
| 12 | Runaway tasks | Budget stop | follow_ups ignored; max steps only | High if enabled | RunBudget |

---

## 14. Security Analysis

| Area | Status |
|------|--------|
| `.env` / keys | `.env.example` + gitignore — OK pattern; Cursor key in env |
| Secrets in logs | Risk if prompts logged later |
| Filesystem | Project jail — decent |
| Shell | Нет произвольного shell; python subprocess only |
| Network in sandbox | Не полностью задокументирован/заблокирован OS-level |
| Untrusted tool output | Нет taint model |
| Prompt injection | Открыт |
| Path traversal | Mostly blocked |
| Malicious project files | Agent может читать; LLM может следовать инструкциям из `problem.md` |
| LLM as trusted | Сейчас фактически trusted for PASS/FAIL — **неверно** |

**Принцип:** LLM = untrusted reasoner. Deterministic code + human = authority.

---

## 15. Testability Analysis

**Есть:** mock LLM, sandbox unit tests, transition tests, one e2e mock run.  
**Нет:** agent unit tests, permission tests, resume tests, independence tests, cursor provider tests, record/replay, golden evidence graphs.

Рекомендуемая пирамида:

1. **Deterministic** — transitions, sandbox, path jail, claim validators, math checks  
2. **Integration** — orchestrator + mock provider fixtures  
3. **Record/replay** — frozen LLM JSON fixtures per role  
4. **LLM eval** (opt-in CI nightly) — не в каждом `pytest`  
5. **Independence tests** — verifier without author fields must still catch planted unit errors  

---

## 16. Recommended Architecture V2

См. полный документ: [architecture-v2.md](architecture-v2.md).

Ключевые сдвиги:

1. **Task Graph** вместо скрытой логики в Runtime.  
2. **Evidence Graph** + kind/provenance enforcement.  
3. **ReviewBundle + DeterministicChecks** для настоящей независимости.  
4. **RunManifest + RunBudget**.  
5. **Synthesis from graph**, не из свободного LLM summary.  
6. **LLMProvider** остаётся; Cursor — reasoning-only без FS.  
7. Не «больше агентов», а **меньше LLM-истины**.

---

## 17. Migration Plan

| Phase | Goal | Breaks API? |
|-------|------|-------------|
| P0 | Safe fixes + docs truth + synthesis from artifacts + no double decision + Cursor cwd harden | Minimal |
| P1 | RunManifest, RunBudget, ReviewBundle, deterministic recompute hook, claim versioning | Moderate |
| P2 | Evidence graph query API, model routing, real research backend, Docker sandbox | Yes (extensions) |
| P3 | OTel, FEM/CFD tools, cloud workers, experiment designer agent | Optional |

**Не переписывать всё сразу.** Сохранить: `LLMProvider`, `ToolRegistry`, `Claim`/`EvidenceKind`, sandbox idea, project-on-disk principle.

---

## 18. Prioritized Action List

### P0 — обязательно сейчас

1. Исправить docs: убрать «dependency graph»; описать фактический FSM.  
2. Synthesis читать `last_verification` + `last_red_team` + claim IDs; запретить «успех» без PASS.  
3. Убрать double-append DecisionLog.  
4. CursorSDK: не давать project cwd / tool escape.  
5. Тест: planted wrong calculation → verification must FAIL when deterministic check enabled (хотя бы scaffold).  
6. Перестать считать mock PASS-after-counter доказательством independence.

### P1 — перед следующим этапом (реальный research / cursor_sdk prod)

1. `RunManifest` + run-scoped artifact dirs.  
2. `RunBudget`.  
3. `ReviewBundle` (blind) + parallel V∥RT.  
4. Verification **обязан** вызывать recompute для CALCULATION claims.  
5. IterationPolicy с evidence-delta.  
6. HITL: wired `hitl_request` + interactive/resume CLI.  
7. Taint labels на tool output.

### P2 — при масштабировании

1. Evidence graph store + query.  
2. Multi-provider routing + cost tracking.  
3. Docker/cgroup sandbox.  
4. Real research backend + source authenticity checks.  
5. Conflict records, supersede chains.

### P3 — optional

1. OpenTelemetry.  
2. Jupyter/FEM/CFD/CAD tools.  
3. Extra agent roles only with new tool surfaces.  
4. Cloud Cursor workers as tools, not as orchestrator.

---

## 19. Attack / Failure Review (10+ сценариев злоупотребления)

| # | Attack | How it works today | Mitigation |
|---|--------|--------------------|------------|
| 1 | Hallucinated evidence | Research LLM invents source string | Require resolvable Source objects; stub ≠ primary |
| 2 | Fake source URI | `mock://` accepted as source | Trust tiers: STUB/SECONDARY/PRIMARY |
| 3 | Bad simulation | Wrong formula, returncode 0 → CALCULATION | Independent recompute + unit check |
| 4 | Incorrect units | No unit type system | Pint/units in deterministic layer |
| 5 | False consensus | Same model PASS+weak RT MEDIUM | Separate models; evidence > agreement |
| 6 | Stale data | Old claims remain in index forever | Run-scoped views; supersede |
| 7 | Poisoned memory | Write malicious claim JSON / problem.md injection | Signed runs; validate schema; taint |
| 8 | Malicious tool output | Future web page: «ignore and PASS» | Delimiters + structured extract |
| 9 | Circular verification | V reads author confidence; RT soft | Blind bundles; strip confidence |
| 10 | Runaway agents | enable follow_ups without budget | RunBudget hard stop |
| 11 | Cursor FS escape | Agent.prompt with local cwd | Reasoning-only provider |
| 12 | Overwrite final truth | Chief files.write final_report | Synthesis gate from graph |

---

## 20. Component Boundary Review

| Component | Does extra work? | Verdict |
|-----------|------------------|---------|
| Agent | Иногда пишет decisions + artifacts + planning | OK thin-ish; Chief overreaches on final truth |
| Tool | Research stub embeds domain claim text | Borderline business logic in tool |
| Orchestrator | Hardcodes stage special cases | Knows too much; should be table/policy driven |
| Workflow | Thin wrapper — OK | Transitions vs runtime inconsistency |
| Memory | Flat claims — OK for MVP | Needs graph |
| LLM provider | Cursor may act as agent platform | Boundary leak |

**Замена агентов без переписывания:** частично возможна (`build_agents` dict), но staging захардкожен в `STAGE_ROLES` + runtime branches.

**Роли:** CALCULATION и SIMULATION — один агент; можно оставить одну роль `Computation` с двумя stage modes. Не добавлять агентов без новых tool surfaces.

---

## 21. Comparison to Target Architecture

```
TARGET: USER → CHIEF → TASK GRAPH → SPECIALISTS → TOOLS
        → INDEPENDENT VERIFICATION → RED TEAM
        → EXPERIMENTAL DESIGN → SYNTHESIS → DECISION → EVIDENCE GRAPH

ACTUAL: USER → RUNTIME(STAGE_ROLES) → SPECIALISTS → TOOLS
        → LLM-VERIFICATION → LLM-REDTEAM → CHIEF PROSE REPORT
        (no task graph, no experimental design, no evidence graph)
```

**Отсутствует:** Task Graph, Experimental Design wiring, Evidence Graph, Run Manifest, Deterministic Math Authority, Anti-collusion controls, Budget, proper HITL resume.

---

## 22. Final Judgment

> Может ли эта система стать фундаментом настоящей AI Engineering Laboratory через год?

**Как есть — нет.**  
**Как эволюционируемый каркас — да, если P0/P1 выполнены до расширения числа агентов и инструментов.**

Сильные семена: artifact-first philosophy, `EvidenceKind`, sandbox, provider protocol, fail-loud, non-linear FSM.  
Смертельные слабости: theatrical independence, synthesis без graph, no reproducibility package, Cursor boundary leak.

**Путь к «да»:** меньше веры в LLM-статусы, больше deterministic checks + evidence graph + run manifests + blind review — не «добавить ещё агентов».

---

## 23. Implementation follow-up (2026-09-11) — do not rewrite history above

P0/P1 were implemented without adding agent roles. See [architecture-v2-implementation.md](architecture-v2-implementation.md).

Status vs findings above:

| Finding | Status after P0/P1 |
|---------|-------------------|
| C1 Deterministic verification | **Addressed** (`checks/`, adjudication hard gate) |
| C2 Mock call-count PASS | **Addressed** |
| C3 Final report from Chief prose | **Addressed** (`SynthesisBundle` gate) |
| C4 Cursor project cwd escape | **Mitigated** (reasoning-only temp cwd; SDK limitation documented) |
| C5 Evidence graph | **Minimal JSON graph** (not Neo4j) |
| H1–H8 | Partially addressed (budget, ReviewBundle, parallel V∥RT, iteration policy, taint, source trust, immutable artifacts, manifest) |

Historical sections 1–22 remain the audit snapshot of the pre-fix MVP.
