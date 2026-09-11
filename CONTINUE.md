# Как продолжить работу на другом устройстве

Краткий алгоритм, если этот чат Cursor недоступен.

## 1. Клонировать репозиторий

```bash
git clone https://github.com/EvdokimovAleksandr/AI-Engineering-Lab.git
cd AI-Engineering-Lab
```

## 2. Поднять окружение

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# Linux/macOS:
# source .venv/bin/activate

pip install -e ".[dev]"
pytest
```

## 3. Прогнать demo без API

```bash
python -m ai_lab run projects/spider_silk_industrial --provider mock
```

## 4. (Опционально) Cursor SDK

1. Ключ: Cursor Dashboard → Integrations → `CURSOR_API_KEY`
2. `.env.example` → `.env`
3. `pip install -e ".[cursor]"`
4. `python -m ai_lab run projects/spider_silk_industrial --provider cursor_sdk`

## 5. Что читать дальше

| Файл | Зачем |
|------|--------|
| [README.md](README.md) | обзор и CLI |
| [docs/architecture.md](docs/architecture.md) | архитектура, MVP vs extension points |
| [config/default.yaml](config/default.yaml) | provider, модели, sandbox, HITL |
| [projects/spider_silk_industrial/problem.md](projects/spider_silk_industrial/problem.md) | первый benchmark-проект |

## 6. Новый чат в Cursor — стартовый промпт

```
Открой репозиторий AI-Engineering-Lab.
Это мультиагентная AI-лаборатория (MVP уже есть).
Прочитай README.md и docs/architecture.md.
Дальше помоги с: <твоя задача>.
Не ломай abstraction LLMProvider / ToolRegistry / независимую Verification+RedTeam.
```

## 7. Принцип системы (не забывать)

AI не должен просто давать ответ. Нужна проверяемая цепочка: research → hypotheses → calculation/simulation → independent verification → red team → synthesis. Состояние — в файлах `projects/`, не в истории чата.
