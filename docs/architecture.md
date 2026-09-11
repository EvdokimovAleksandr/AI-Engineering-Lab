# Architecture — AI Engineering Lab (post V2 P0/P1)

> Исторический аудит: [architecture-audit.md](architecture-audit.md)  
> Целевая модель: [architecture-v2.md](architecture-v2.md)  
> Что реализовано: [architecture-v2-implementation.md](architecture-v2-implementation.md)

## Цель

Повышать вероятность **корректного** инженерного результата за счёт декомпозиции, **deterministic checks**, независимой проверки (blind ReviewBundle), red team и adjudication — не за счёт объёма текста или числа агентов.

## Фактическая оркестрация

Оркестратор использует **таблицу** `STAGE_ROLES` (stage → roles) + FSM. Это **не** dependency graph.

На стадии `VERIFICATION` runtime выполняет:

1. `DeterministicCheckReport` (MathCheck / recompute)
2. Frozen `ReviewBundle` (без author confidence)
3. **Параллельно** Verification ∥ Red Team (общий bundle, без результатов друг друга)
4. `Adjudication` (LLM не может перекрыть deterministic FAIL → PASS)
5. PASS → SYNTHESIS | иначе → `IterationPolicy` re-entry

## Слои

| Слой | Ответственность |
|------|-----------------|
| `core/` | Enums, модели (Claim versioning, RunManifest, ReviewBundle, Graph…) |
| `checks/` | Deterministic MathCheck |
| `llm/` | `MockProvider`, `CursorSDKProvider` (reasoning-only) |
| `tools/` | Registry + permissions + **trust_level** taint |
| `memory/` | ProjectStore, EvidenceStore, EvidenceGraph, RunStore, ReviewBundle |
| `agents/` | 6 ролей (без новых) |
| `workflows/` | FSM + transitions |
| `orchestrator/` | LabRuntime, budget, adjudication, synthesis gate, iteration policy |

## Поток данных

```
USER → Chief → specialists → tools → artifacts / evidence graph
  → deterministic checks → ReviewBundle
  → Verification ∥ Red Team → Adjudication
  → FAIL: IterationPolicy | PASS: SynthesisBundle → final_report.md
```

## Final report gate

`final_report.md` строится из `SynthesisBundle` (accepted/disputed/rejected + V/RT + residual risks).  
LLM может добавить prose, но **не** определяет, что доказано.  
DISPUTED/FAIL/INCOMPLETE явно маркируются как недоказанные.

## Reproducibility

Каждый run: `projects/<name>/.runs/<run_id>/manifest.json` + immutable `computations/*.json`.

## Safety

- Python sandbox (AST whitelist, timeout, isolated `-I`)
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
```

## Knowledge layer (V2.1)

Claims are **run-scoped** (`.runs/<run_id>/claims/`). Agents use visibility policies (`CURRENT_RUN` / `PROJECT_HISTORY` / `APPROVED_KNOWLEDGE`). Evidence graph + query API live under `knowledge/`. See [knowledge-architecture.md](knowledge-architecture.md).
