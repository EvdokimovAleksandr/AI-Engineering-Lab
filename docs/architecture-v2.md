# Architecture V2 — AI Engineering Lab

Целевая архитектура после аудита MVP. Принцип: **проще там, где MVP переусложнил ролями; строже там, где MVP был театральным**.

Связанные документы:
- Аудит: [architecture-audit.md](architecture-audit.md)
- Реализация P0/P1: [architecture-v2-implementation.md](architecture-v2-implementation.md)
- Текущее поведение кода: [architecture.md](architecture.md)

**Статус:** P0/P1 реализованы в коде (deterministic checks, ReviewBundle, parallel V∥RT, adjudication, RunManifest/Budget, synthesis gate, claim versioning, tool taint). Ниже — целевая модель; пункты без пометки «done in P1» остаются roadmap.

---

## 1. Design principles

1. **LLM = reasoning / planning / interpretation**, never sole authority for PASS/FAIL or numeric truth.
2. **Deterministic software = calculation, validation, transformation, gates.**
3. **External sources = evidence with trust tier**, not free-text «source» strings.
4. **Artifacts on disk = system of record**; chat is ephemeral.
5. **Independent review = blind bundle + recompute**, not a second polite LLM.
6. **CONSENSUS ≠ INDEPENDENT_EVIDENCE.**
7. **Fewer agents; more tools and checks.**
8. **Fail loud**; no silent fallbacks (сохраняем философию MVP).
9. **Every run is an experiment** with a manifest.
10. **Human is authority** for ambiguous / high-risk decisions.

---

## 2. Target flow

```
USER
  └─► Project (problem, constraints, risk class)
        └─► ChiefEngineer (plan only → TaskGraph)
              └─► Specialists (Research / Theorist / Computation)
                    └─► Tools (sandbox, research, simulators…)
                          └─► EvidenceGraph (versioned nodes)
                                ├─► DeterministicChecks
                                ├─► Verification (blind, optional separate model)
                                └─► RedTeam (parallel, separate model)
                                      └─► Adjudicator / HITL
                                            └─► Synthesis (template from graph)
                                                  └─► DecisionRecord (accepted | rejected | needs_evidence)
```

Experimental Design — отдельная стадия **только когда** risk class / HITL требует эмпирики; не обязательна в каждом run.

---

## 3. Layering (keep what works)

| Layer | Keep from MVP | Change |
|-------|---------------|--------|
| `core/` | enums, protocols, pydantic | + EvidenceGraph types, RunManifest, RunBudget, ReviewBundle |
| `llm/` | `LLMProvider` protocol, mock, factory | + routing; Cursor **reasoning-only**; OpenAI-compatible later |
| `tools/` | registry, permissions, python sandbox | + taint; resource limits; new tools without agent rewrites |
| `memory/` | ProjectStore path jail | + run-scoped dirs; graph store; no giant context |
| `agents/` | thin role classes | strip authority; no final-truth write without gate |
| `workflows/` | FSM idea | IterationPolicy; stage ledger; less runtime hardcode |
| `orchestrator/` | LabRuntime loop | execute TaskGraph + budgets + gates |
| `checks/` **NEW** | — | units, recompute, bounds, schema |
| `observability/` | JSONL events | + prompt/tool I/O hashes in manifest |

**Не ломать:** `LLMProvider`, `ToolRegistry`, независимые роли Verification/RedTeam как *отдельные процессы* (усилить, не слить в один «reviewer»).

---

## 4. Core data model

### 4.1 Knowledge kinds (keep + enforce)

```
FACT | ASSUMPTION | HYPOTHESIS | INFERENCE
CALCULATION | SIMULATION_RESULT | EXPERIMENT_RESULT | OPINION
```

Правила:

- `FACT` требует `Source` с `trust_tier ∈ {PRIMARY, SECONDARY}` (не STUB).
- `CALCULATION` / `SIMULATION_RESULT` требуют `ComputationArtifact` (code hash, inputs, stdout, returncode).
- Запрещено повышать kind без нового evidence node + decision.

### 4.2 Evidence Graph

```text
NodeType:
  Source | Claim | Hypothesis | Computation | CheckReport |
  VerificationReport | RedTeamReport | Decision | Conflict

EdgeType:
  CITES | SUPPORTS | CONTRADICTS | DERIVES_FROM |
  RECOMPUTES | SUPERSEDES | REVIEWS | DECIDES
```

Каждый node: `id`, `run_id`, `created_at`, `agent_role?`, `content_hash`, `payload`.

Query example:

```
Decision D → DECIDES → Claim C
Claim C → DERIVES_FROM → Computation X
Computation X → RECOMPUTES ← CheckReport K
Claim C → CITES → Source S
```

### 4.3 ReviewBundle (blind package)

```yaml
review_bundle:
  bundle_id: rb_...
  target_claim_ids: [...]
  claims:            # stripped: no author confidence narrative, no "how we got here" prose
    - claim_id
    - statement
    - kind
    - conditions
    - assumptions
    - falsifiers
    - computation_ref?   # code + inputs only
  author_fields_excluded: [confidence, agent_id, free_text_evidence_essay]
```

Verification и Red Team получают **только** bundle (+ CheckReport для V). Не весь project dump.

### 4.4 RunManifest

```yaml
run_id: run_...
project: spider_silk_industrial
created_at: ...
code_version: git_sha
python_version: "3.13.1"
dependencies_hash: ...
config_hash: ...
provider: cursor_sdk
model_routing:
  verification: model_a
  red_team: model_b
  default: model_c
inputs:
  problem_md_sha256: ...
budget:
  max_steps: 24
  max_tool_calls: 100
  max_tokens: 500000
  max_cost_usd: 20
  max_runtime_sec: 3600
stages: []  # ledger entries
artifact_root: projects/.../.runs/run_.../
```

### 4.5 RunBudget (enforced)

Перед каждым agent/tool call:

- `max_steps`, `max_agent_calls`, `max_tool_calls`
- `max_tokens`, `max_cost_usd` (providers report usage)
- `max_runtime_sec`
- soft warning → hard `AWAITING_HUMAN` / `BUDGET_EXCEEDED`

---

## 5. Independent verification design

### 5.1 Split authority

| Step | Owner |
|------|-------|
| Parse calculation claim → extract code/inputs | Deterministic |
| Re-run code in sandbox | `python.execute` / checks service |
| Compare numeric tolerance / units | Deterministic |
| Interpret semantic discrepancies | Verification LLM (optional) |
| Final gate PASS | Rules: all critical checks green **and** no unresolved CRITICAL RT (or HITL accept) |

### 5.2 Anti-collusion controls

1. **Different models** for author vs verification vs red team when multiple providers available.
2. **Parallel** V and RT on same frozen bundle.
3. **No shared draft workspace** during review.
4. Record `agreement_type`: `CONSENSUS` | `INDEPENDENT_EVIDENCE` | `MIXED`.
5. Majority vote **без** independent evidence **не** повышает confidence.

### 5.3 What Mock must prove

Mock PASS only if:

- planted unit error fixture → FAIL, or
- evidence-delta after iteration includes new computation node that fixes check.

Счётчик вызовов — запрещён как критерий PASS.

---

## 6. Workflow V2

### 6.1 Still a state machine — but with policies

Keep linear backbone for MVP simplicity. Add:

```text
IterationPolicy:
  on Verification FAIL:
    if discrepancies contain "units"|"recompute" → reenter COMPUTATION
    if "assumption" → reenter ANALYSIS
    if "missing source" → reenter RESEARCH
    else → ANALYSIS
```

### 6.2 TaskGraph (minimal)

Chief (or static template) emits tasks:

```text
TaskSpec:
  id, role, objective
  input_node_ids[]      # not free paths only
  output_schema
  independence_group
  budget_slice
```

Orchestrator **исполняет граф**, а не игнорирует `follow_up_tasks`.  
Для ранних проектов допустим **static template graph** (как сейчас STAGE_ROLES) — но как данные, не как скрытая логика Runtime.

### 6.3 Resume

- Stage ledger в RunManifest: `pending|running|committed|failed`
- Commit = atomic write to run-scoped dir + graph transaction
- `ai_lab resume <run_id>` продолжает тот же manifest

### 6.4 HITL levels

| Level | Examples |
|-------|----------|
| AUTO | sandbox math, schema validate |
| REVIEW_REQUIRED | weak sources, disputed medium |
| HUMAN_APPROVAL_REQUIRED | CRITICAL red team, expensive experiment, physical risk, budget override |

Wire `AgentResult.hitl_request`. Interactive CLI or file-based decision drop.

---

## 7. Agent roles (consolidate, don't inflate)

| Role | Responsibility | LLM? |
|------|----------------|------|
| Chief Engineer | Problem framing, task graph, contradiction watch, HITL liaison | Yes |
| Research | Source acquisition via tools; structured findings | Yes + tools |
| Theorist | Models, assumptions, falsifiers | Yes (+ sympy tool later) |
| Computation | Code + interpret (merge CALCULATION+SIMULATION stages) | Yes + **required** execute |
| Verification | Blind review + consume CheckReports | Yes (narrow) + checks |
| Red Team | Attack assumptions / physics / numerics | Yes (narrow) |
| Deterministic Checks | units, recompute, bounds | **No LLM** |
| Experimental Scientist | Only when experiment stage exists | Later |
| Engineering Designer | Only with CAD/design tools | Later |

Synthesis: **mostly deterministic template**; Chief may polish language but cannot invent PASS.

---

## 8. Tool architecture

```text
ToolSpec:
  name, description, handler
  permission_scopes
  trust_out: TRUSTED | UNTRUSTED
  resource_limits: {timeout, mem, cpu, out_bytes, network: deny|allow}
```

Extension: register FEM/CFD/CAD/MCP as new tools — agents get names via config `allowed_tools`, без rewrite.

Sandbox roadmap:

1. Now: subprocess AST (keep)
2. Next: network deny at OS level, memory limit
3. Later: Docker/gVisor

---

## 9. Memory architecture

```text
Short-term: per-task context assembler (explicit, small)
Run scratch: projects/<p>/.runs/<run_id>/
Project memory: claims/decisions surviving across runs (with supersede)
Evidence graph: queryable index
Knowledge base (optional later): curated sources outside one project
```

Запрет: `list_claims()` dump всего проекта в каждый LLM call.

---

## 10. Multi-model extension points

```text
LLMRouter.complete(request, role) ->
  pick provider+model from config.models[role]
  record usage into RunBudget + Manifest
```

Позже: retries with classified errors, rate limits, cost tracking.  
**Не** строить сложный mesh в P0.

CursorSDK: `cwd=tempempty`, prompt: «JSON only, no tools/files/shell». Lab tools remain sole side-effect channel.

---

## 11. Observability / tracing

Каждый шаг пишет:

- agent_role, model, provider, prompt_hash, response_hash
- tool_name, tool_args_hash, tool_result_hash, trust_out
- workflow transition reason
- verification gate inputs (check IDs) → status

Вопросы аудита («почему PASS?») отвечаются из manifest+graph, не из чата.

---

## 12. Security posture V2

1. LLM untrusted.
2. Tool outputs untrusted unless `trust_out=TRUSTED` (sandbox math stdout can be trusted as bytes, not as semantic claim).
3. Delimit untrusted text in prompts.
4. No Cursor/local agent FS on project.
5. Secrets never in artifacts.
6. Path jail + run-scoped writes for agents where possible.
7. Malicious `problem.md` treated as untrusted input.

---

## 13. Testing strategy V2

| Layer | Mechanism |
|-------|-----------|
| Deterministic | checks, transitions, sandbox, path jail |
| Integration | mock provider + golden fixtures |
| Independence | planted bad calc must fail checks even if LLM says PASS |
| Record/replay | saved LLM JSON |
| Nightly LLM | optional, not default pytest |

---

## 14. Migration mapping (files)

| Change | Likely files | Phase |
|--------|--------------|-------|
| Docs honesty | `docs/architecture.md`, README | P0 |
| Fix double DecisionLog | `agents/chief_engineer.py` **or** `orchestrator/runtime.py` | P0 |
| Synthesis from reviews | `agents/chief_engineer.py`, maybe new `synthesis.py` | P0 |
| Cursor cwd harden | `llm/cursor_sdk.py` | P0 |
| Mock verification evidence-delta | `llm/mock.py`, new tests | P0/P1 |
| RunManifest + budget | `core/models.py`, `orchestrator/runtime.py`, `config/default.yaml` | P1 |
| ReviewBundle | `memory/`, `agents/verification.py`, `agents/red_team.py` | P1 |
| Deterministic checks package | `src/ai_lab/checks/` new | P1 |
| IterationPolicy | `workflows/` | P1 |
| Evidence graph | `memory/graph.py`, models | P2 |
| Provider router | `llm/registry.py` | P2 |

---

## 15. What we deliberately do NOT do in V2 MVP

- Не добавлять 5 новых агентов.
- Не заменять FSM на тяжёлый distributed workflow engine.
- Не требовать Kubernetes.
- Не делать «voting committee» из 10 LLM.
- Не хранить истину в vector DB без provenance.

---

## 16. Success criteria (год)

Система готова масштабироваться, если:

1. Любой final decision раскрывается в evidence graph path.  
2. Чужой run воспроизводится по RunManifest (mock/deterministic parts bit-identical; LLM parts model-pinned).  
3. Planted arithmetic bug ловится без «надежды на verifier prompt».  
4. Новый tool (SymPy/FEM) подключается без изменения Verification кода.  
5. Budget останавливает runaway.  
6. Human видит только AUTO/REVIEW/APPROVAL по политике риска.  
7. Verification и Red Team не могут читать author chain-of-thought.

Пока пункты 1–3 и 7 не выполнены — это прототип оркестратора, не лаборатория.
