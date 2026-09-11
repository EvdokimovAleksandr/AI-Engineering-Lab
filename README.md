# AI Engineering Lab

Мультиагентная лаборатория для исследования инженерных и научных задач с независимой верификацией, red team и проверяемой цепочкой доказательств.

Это **не chatbot**. Состояние живёт в артефактах проекта на диске, а не в истории чата.

## Принцип

```
Problem → Decomposition → Research → Hypotheses → Analysis → Calculation
→ Simulation → Verification → Red Team → Synthesis
```

Несогласие агентов — нормальный и желаемый исход. Каждое существенное утверждение должно иметь вид, источник/обоснование, допущения, falsifiers и confidence breakdown.

## MVP (что уже есть)

- 6 агентов: Chief Engineer, Research, Theorist, Simulation, Verification, Red Team
- Workflow state machine (нелинейный: FAIL → ITERATION_REQUIRED)
- Project memory + Decision Log (JSONL)
- Tools: Python sandbox, files, artifacts, research stub, logging
- LLM abstraction: `mock` (по умолчанию) и `cursor_sdk` (ваш аккаунт Cursor)
- Scaffold проекта `projects/spider_silk_industrial/`

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

`CursorSDKProvider` использует one-shot `Agent.prompt` только как reasoning backend. Compute и запись артефактов идут через tool layer лаборатории.

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
python -m ai_lab run <project_name_or_path> [--provider mock|cursor_sdk] [--config path]
```

## Философия

AI не должен просто давать ответ. Он должен строить проверяемую цепочку рассуждений, доказательств, вычислений и попыток опровержения.
