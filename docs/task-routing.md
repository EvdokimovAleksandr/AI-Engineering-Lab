# Task Router

## Зачем нужен Router

Существующий `STAGE_ROLES` / FSM и `TaskGraph` описывают **как** исполнять работу.
Task Router отвечает на вопрос **какой минимальный профиль** нужен **до** планирования:

```text
USER TASK
    ↓
TASK ROUTER (classify)
    ↓
TASK CLASSIFICATION (proposal)
    ↓
TaskRoutingPolicy (deterministic floors)
    ↓
RoutingDecision → workflow profile → TaskGraph template
```

Router **не решает** инженерную задачу. Он выбирает глубину evidence и профиль pipeline.

Это **не** `LLMRouter` (V2.4a): тот выбирает `ModelConfig` на роль. Task Router выбирает `WorkflowProfile`.

## Оси классификации

Структурированная модель `TaskClassification`:

| Поле | Смысл |
|------|--------|
| `task_type` / `domain` | Тип и предметная область |
| `complexity` 0–10 | Сложность (не риск) |
| `risk` 0–10 | Опасность / цена ошибки (отдельная ось) |
| `uncertainty` 0–10 | Нехватка знаний / противоречивость |
| `required_evidence` | Минимальный набор evidence |
| `recommended_workflow` | Предложение классификатора |
| `reasoning` / `confidence` | Пояснение proposal |

### Complexity

| Score | Band |
|-------|------|
| 0–2 | SIMPLE |
| 3–5 | STANDARD |
| 6–8 | COMPLEX |
| 9–10 | RESEARCH |

Число **не** является единственным решением — смотри Policy.

### Risk

| Score | Band |
|-------|------|
| 0–2 | LOW |
| 3–5 | MEDIUM |
| 6–8 | HIGH |
| 9–10 | CRITICAL |

Низкая complexity **не** упрощает workflow при высоком risk.
Пример: простая формула на safety-critical системе → SIMPLE complexity + HIGH risk → policy поднимает verification / HITL.

### Uncertainty

| Score | Band |
|-------|------|
| 0–2 | LOW |
| 3–5 | MEDIUM |
| 6–8 | HIGH |
| 9–10 | VERY HIGH |

Высокая uncertainty повышает требования к research/evidence.

## Required evidence

```text
DETERMINISTIC
DETERMINISTIC_PLUS_VERIFICATION
VERIFICATION_PLUS_REDTEAM
RESEARCH_PLUS_VERIFICATION
FULL_RESEARCH_CYCLE
HUMAN_REVIEW
```

Policy выбирает **минимум**; классификатор может предложить больше, но не меньше floors.

## LLM classification vs policy

```text
Classifier proposal  →  TaskRoutingPolicy.apply()  →  RoutingDecision
```

Правило: **policy may only raise** workflow/evidence ranks. LLM/heuristic **не может** снизить обязательный уровень.

Конфиг: `config/default.yaml` → `task_routing:` (правила и пороги). Встроенные defaults: high risk, high uncertainty, all-axes-low → SIMPLE candidate.

## Workflow profiles

| Profile | Состав (поверх существующего TaskGraph) |
|---------|------------------------------------------|
| SIMPLE | understanding → calculation → deterministic_verify → adjudication → synthesis |
| STANDARD | + decomposition/analysis + verification (без red team) |
| COMPLEX | specialists + V ∥ RT + adjudication |
| RESEARCH | полный default pipeline (research → hypotheses → …) |

`simulation.pipeline: uniaxial_tension` остаётся явным trusted override и **не** переписывается Router'ом.

## Примеры

### Simple heater

complexity≈1, risk≈0, uncertainty≈0 → policy `all_axes_low` → **SIMPLE**.
`ProblemKind.CLOSED_NUMERIC` (ScopeResolver) усиливает calculation hint; policy не понижает уже поднятый профиль.

### Shaft design

engineering design signals → **STANDARD**; SIMPLE запрещён evaluation'ом.

### Spider silk review

research / competing approaches → **RESEARCH**.
`ProblemKind.RESEARCH_REVIEW` raise-only поднимает workflow до RESEARCH, если классификатор недооценил.

### Open-ended «how to make the rod stronger?»

`ProblemKind.OPEN_ENDED` → contract `NEEDS_CLARIFICATION`; HITL только для Required (`load type: axial / bending / combined?`). Полный RESEARCH/DESIGN pipeline не стартует, пока Required не заполнены.

### High-risk «простая» задача

Classifier может поставить `recommended_workflow=SIMPLE` при `risk=8`, но policy rule `high_risk` поднимет до **COMPLEX** + `VERIFICATION_PLUS_REDTEAM`.

## Артефакты run

```text
projects/<name>/.runs/<run_id>/planner/task_routing.json
RunManifest.workflow_profile
RunManifest.task_routing_decision
```

## Код

- `src/ai_lab/task_routing/` — models, policy, classifier, profiles, router
- `src/ai_lab/orchestrator/scope_resolver.py` — problem kind + Required frame
- Интеграция: `LabRuntime._route_task` → `create_planner(..., pipeline_override=...)`
