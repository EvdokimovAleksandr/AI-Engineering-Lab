# Benchmarks

Эталонные задачи и focused scenarios для измерения поведения лаборатории
(routing, clarification, isolation, units, budget-stop — не только числа).

## CLI

```bash
python -m ai_lab benchmark list
python -m ai_lab benchmark run simple_heater
python -m ai_lab benchmark run ambiguous_rod_strength
python -m ai_lab benchmark evaluate simple_heater <run_id>
python -m ai_lab benchmark scenario --all
python -m ai_lab benchmark scenario ambiguous_engineering
```

Каждый `run` копирует scaffold в изолированный workspace
`benchmarks/.workspace/<id>/` и сохраняет обычный `RunManifest` под
`.runs/<run_id>/`. Claims/evidence **не** смешиваются с другими проектами.

В отчётах CLI / evaluate / UI печатается **MODE: MOCK | LIVE_CURSOR | REPLAY**
(из `model_provider`), чтобы MOCK/STUB research не путали с LIVE failure.

## Project benchmarks

| Id | Expected behavior | Workflow |
|----|-------------------|----------|
| `simple_heater` | **PASS** (closed calc; mock fixture `heater_correct`) | SIMPLE |
| `shaft_design` | PASS (preliminary diameter) | STANDARD |
| `spider_silk_review` | **ORCHESTRATION_ONLY** (STUB ≠ PASS) | RESEARCH |
| `ambiguous_rod_strength` | **NEEDS_CLARIFICATION** (`load_type`) | stop before fake PASS |

### `simple_heater`

Закрытый расчёт мощности нагревателя (20 L, 20→80°C, 30 min, 15% losses).
Под mock по умолчанию используется `simulation_fixture=heater_correct` для
PASS-path. См. [benchmark-integrity.md](benchmark-integrity.md).

### `shaft_design`

Предварительный диаметр стального вала (10 kW @ 1500 rpm + factor of safety).
Under-routing to SIMPLE = **FAIL**.

### `spider_silk_review`

Обзор промышленных подходов к spider silk. Research backend может быть **MOCK**.
Оценивается orchestration; пустой/STUB research **не** engineering PASS.

Spider silk 2.0 scaffold (без 20 solvers):
[`benchmarks/spider_silk_review/phases/00_problem_definition/`](../benchmarks/spider_silk_review/phases/00_problem_definition/) —
cost bottleneck при заданной system boundary.

### `ambiguous_rod_strength`

«Как сделать стержень прочнее?» → OPEN_ENDED + HITL на `load_type`.
Фейковый numeric PASS = **FAIL**.

## Focused scenarios (`benchmark scenario`)

Детерминированные проверки без полного project tree (PR-07):

| Id | Expected |
|----|----------|
| `context_isolation` | sofa+rod → FailureClass.**CONTEXT** (not PASS) |
| `units` | length+m pass; length vs litre fail |
| `scope_resolution` | closed rod → CLOSED_NUMERIC |
| `ambiguous_engineering` | open rod → NEEDS_CLARIFICATION |
| `budget_control` | IterationController → STOP_INSUFFICIENT_EVIDENCE |
| `research_review` | RESEARCH kind; STUB ≠ PASS |

## Evaluation

`EvaluationReport` categories include PR-07 hooks:

- `expected_behavior` — PASS / NEEDS_CLARIFICATION / INSUFFICIENT_EVIDENCE / ORCHESTRATION_ONLY
- `failure_class` — aggregates existing PR-06 `FailureClass` (no duplicate enum)
- `details.execution_mode` — MOCK | LIVE_CURSOR | REPLAY

Вердикты: `PASS` | `PARTIAL` | `FAIL` | `NOT_APPLICABLE`.

См. также: [benchmark-integrity.md](benchmark-integrity.md), [task-routing.md](task-routing.md).
