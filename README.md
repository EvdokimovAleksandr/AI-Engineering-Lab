# AI Engineering Lab

Мультиагентная лаборатория для исследования инженерных и научных задач с независимой верификацией, red team и проверяемой цепочкой доказательств.

Это **не chatbot**. Состояние живёт в артефактах проекта на диске, а не в истории чата.

## Принцип

```
Problem → Planner → validated TaskGraph → Research → Hypotheses → Analysis → Calculation
→ Simulation → [Deterministic Checks → Verification ∥ Red Team → Adjudication]
→ Synthesis (gated) | IterationPolicy
```

Несогласие агентов — нормальный и желаемый исход. Каждое существенное утверждение должно иметь вид, источник/обоснование, допущения, falsifiers и confidence breakdown.

## MVP / V2 (что уже есть)

- 6 агентов: Chief Engineer, Research, Theorist, Simulation, Verification, Red Team
- Validated `TaskGraph` (StaticPlanner / LLM proposal); `STAGE_ROLES` — compatibility table, не execution DAG
- Parallel independent review: Verification ∥ Red Team + Adjudication
- Deterministic MathCheck + Pint `DeterministicVerifier`; LLM cannot override critical FAIL
- SynthesisBundle → gated `final_report.md`
- RunManifest + immutable computation artifacts under `.runs/<run_id>/`
- RunBudget (agent/tool/token/time/cost caps)
- Evidence graph (JSON), claim versioning, ReviewBundle (blind)
- Tools: Python compute sandbox (`python.execute` → LocalSubprocessSandbox или optional DockerSandbox), files, artifacts, research pipeline (EXTERNAL taint; mock:// = STUB)
- LLM: `mock` | `cursor_sdk` (reasoning-only cwd) | `replay`; **LLMRouter** selects per-role `ModelConfig`
- Scaffold: `projects/spider_silk_industrial/`
- Engineering simulation (V2.5): `SimulationSpec` + `UniaxialTensionSolver` + Pint; synthetic tensile fixture is STUB, not FACT
- Local task UI: `python -m ai_lab ui` → LabRuntime (no second orchestrator)

## Knowledge (V2.1)

Run-scoped claims, ApprovedKnowledge, evidence graph queries, conflicts, and migration:
see [knowledge-architecture.md](docs/knowledge-architecture.md) and [knowledge-migration.md](docs/knowledge-migration.md).

## Быстрый старт

```bash
python -m venv .venv
# Windows Git Bash:
source .venv/Scripts/activate
pip install -e ".[dev]"

# Офлайн demo (без API-ключей)
python -m ai_lab run projects/spider_silk_industrial --provider mock

# Тесты
pytest
```

## Cursor SDK (без отдельной LLM-подписки)

1. Создайте ключ: [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations)
2. Скопируйте `.env.example` → `.env` и задайте `CURSOR_API_KEY`
3. Установите extras: `pip install -e ".[cursor]"`
4. В `config/default.yaml` поставьте `provider: cursor_sdk` (или `--provider cursor_sdk`)
5. Модели на роль задаются в `config/default.yaml` → `models:` / `routing:`

`CursorSDKProvider` — **reasoning-only**: temporary empty cwd (not the project root), JSON-only prompts, no FS side effects. All compute/writes go through ToolRegistry. Limitation: if a future SDK ignores cwd isolation, treat as untrusted — see implementation report.

Маршрутизация моделей: [docs/multi-model-routing.md](docs/multi-model-routing.md). Разные модели ≠ независимое доказательство (`INDEPENDENT_EVIDENCE`).

Compute sandbox: [docs/compute-sandbox.md](docs/compute-sandbox.md), Docker backend: [docs/docker-sandbox.md](docs/docker-sandbox.md). `DeterministicVerifier` остаётся отдельным in-process путём. `backend: docker` без Docker Engine падает явно (без fallback на local).

Если выбран `cursor_sdk`, но нет ключа или пакета — система **упадёт явно** (без silent fallback).

## Структура

```
src/ai_lab/          # код лаборатории
config/default.yaml  # provider, models, sandbox, HITL
projects/            # инженерные задачи (память на диске)
docs/architecture.md # архитектура и extension points
tests/               # критические тесты
```

## CLI

```bash
python -m ai_lab run <project_name_or_path> [--provider mock|cursor_sdk] [--config path] [--resume] [--auto-approve-hitl]
python -m ai_lab plan <project_name_or_path> [--provider mock|cursor_sdk]
python -m ai_lab research "<query>" [--research-backend mock|replay|web] [--project path]
python -m ai_lab routing [--provider mock]
python -m ai_lab sandbox
python -m ai_lab ui
```

Архитектура плана: [docs/taskgraph-planner.md](docs/taskgraph-planner.md).  
Симуляция: [docs/engineering-simulation.md](docs/engineering-simulation.md). UI: [docs/ui.md](docs/ui.md).

## Философия

AI не должен просто давать ответ. Он должен строить проверяемую цепочку рассуждений, доказательств, вычислений и попыток опровержения.
