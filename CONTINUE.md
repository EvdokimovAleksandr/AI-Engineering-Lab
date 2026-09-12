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
| [docs/research-pipeline.md](docs/research-pipeline.md) | ResearchProvider, SearchProvider, provenance |
| [docs/engineering-verification.md](docs/engineering-verification.md) | Pint, VerificationSpec, DeterministicVerifier |
| [docs/taskgraph-planner.md](docs/taskgraph-planner.md) | TaskGraph, validator, planner vs runtime |
| [docs/multi-model-routing.md](docs/multi-model-routing.md) | LLMRouter, RoutingPolicy, independence levels |
| [docs/compute-sandbox.md](docs/compute-sandbox.md) | ComputeSpec, LocalSubprocessSandbox, честные Windows guarantees |
| [docs/docker-sandbox.md](docs/docker-sandbox.md) | DockerSandbox, image pinning, HARD network/fs, TCB |
| [docs/engineering-simulation.md](docs/engineering-simulation.md) | SimulationSpec, UniaxialTensionSolver, synthetic tensile benchmark |
| [docs/task-routing.md](docs/task-routing.md) | Task Router: complexity/risk/uncertainty → workflow profiles |
| [docs/benchmarks.md](docs/benchmarks.md) | simple_heater / shaft_design / spider_silk_review |
| [docs/benchmark-integrity.md](docs/benchmark-integrity.md) | V2.6: CalculationSpec, empty verification ≠ PASS, grounded synthesis |
| [docs/ui.md](docs/ui.md) | V2.7 / V2.7.1 laboratory UI: projects, async runs, SSE, run-scoped report |
| [config/default.yaml](config/default.yaml) | provider, модели, sandbox, HITL, research/verification limits |
| [projects/spider_silk_industrial/problem.md](projects/spider_silk_industrial/problem.md) | первый benchmark-проект |

## 6. Новый чат в Cursor — стартовый промпт

```
Открой репозиторий AI-Engineering-Lab.
Это мультиагентная AI-лаборатория (MVP уже есть).
Прочитай README.md и docs/architecture.md.
Дальше помоги с: <твоя задача>.
Не ломай abstraction LLMProvider / ToolRegistry / независимую Verification+RedTeam.
Не переноси математическую проверку внутрь LLM — DeterministicVerifier (Pint + AST) авторитетен для CheckStatus.
LLM planner предлагает TaskGraph; исполняется только граф после deterministic validate_task_graph.
LLMRouter выбирает ModelConfig из validated RoutingPolicy; LLM/research text не может сменить provider/model.
Разные модели ≠ INDEPENDENT_EVIDENCE.
python.execute идёт через ComputeSandbox (LocalSubprocessSandbox или DockerSandbox); DeterministicVerifier не заменяется sandbox'ом.
Инженерная модель — SimulationSpec + SolverRegistry, не «LLM пишет Python в Docker».
UI только ставит задачу в LabRuntime; sandbox/routing/budget из UI задать нельзя.
V2.7: UI — research workstation (не chatbot); async runs + SSE; synthesis не переопределяет engineering_outcome.
V2.7.2: LLM planner предлагает; validator — authority; невалидный граф → 1 retry → StaticPlanner (профиль TaskRouter); роли не remap-ятся.
V2.8: Scope Gate до Planner; HITL clarification через существующий resume; research recovery отдельно от planner recovery; evidence gap ≠ assumption.
```

## 7. Принцип системы (не забывать)

AI не должен просто давать ответ. Нужна проверяемая цепочка: research → hypotheses → calculation/simulation → independent verification → red team → synthesis. Состояние — в файлах `projects/`, не в истории чата.

UI — окно в лабораторию. Orchestration остаётся в LabRuntime.
