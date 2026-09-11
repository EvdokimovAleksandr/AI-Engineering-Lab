# AI Engineering Lab

Мультиагентная лаборатория для исследования инженерных и научных задач с независимой верификацией, red team и проверяемой цепочкой доказательств.

Это **не chatbot**. Состояние живёт в артефактах проекта на диске, а не в истории чата.

## Принцип

```
Problem → Decomposition → Research → Hypotheses → Analysis → Calculation
→ Simulation → [Deterministic Checks → Verification ∥ Red Team → Adjudication]
→ Synthesis (gated) | IterationPolicy
```

Несогласие агентов — нормальный и желаемый исход. Каждое существенное утверждение должно иметь вид, источник/обоснование, допущения, falsifiers и confidence breakdown.

## MVP / V2 (что уже есть)

- 6 агентов: Chief Engineer, Research, Theorist, Simulation, Verification, Red Team
- Stage table `STAGE_ROLES` + FSM (не dependency graph)
- Parallel independent review: Verification ∥ Red Team + Adjudication
- Deterministic MathCheck; LLM cannot override critical FAIL
- SynthesisBundle → gated `final_report.md`
- RunManifest + immutable computation artifacts under `.runs/<run_id>/`
- RunBudget (agent/tool/token/time/cost caps)
- Evidence graph (JSON), claim versioning, ReviewBundle (blind)
- Tools: Python sandbox, files, artifacts, research stub (EXTERNAL taint)
- LLM: `mock` | `cursor_sdk` (reasoning-only cwd)
- Scaffold: `projects/spider_silk_industrial/`

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
5. Модели на роль задаются в `config/default.yaml` → `models:`

`CursorSDKProvider` — **reasoning-only**: temporary empty cwd (not the project root), JSON-only prompts, no FS side effects. All compute/writes go through ToolRegistry. Limitation: if a future SDK ignores cwd isolation, treat as untrusted — see implementation report.

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
```

## Философия

AI не должен просто давать ответ. Он должен строить проверяемую цепочку рассуждений, доказательств, вычислений и попыток опровержения.
