# Architecture — AI Engineering Lab

## Цель

Повышать вероятность **корректного** инженерного результата за счёт декомпозиции, независимой проверки и red team — не за счёт объёма текста или числа агентов.

## Слои

| Слой | Ответственность |
|------|-----------------|
| `core/` | Enums, Pydantic-модели, Protocol'ы. Не зависит от agents/tools. |
| `llm/` | `MockProvider`, `CursorSDKProvider`, factory. |
| `tools/` | Sandbox Python, files, artifacts, research stub, permissions. |
| `memory/` | ProjectStore, EvidenceStore, DecisionLog. |
| `agents/` | Роли лаборатории (тонкие: prompt + валидация + tools). |
| `workflows/` | State machine и переходы. |
| `orchestrator/` | Сборка графа зависимостей и цикл run. |
| `observability/` | Logger + JSONL run events (задел под tracing). |

## Поток данных

1. Human создаёт/выбирает `projects/<name>/` с `problem.md`.
2. `LabRuntime` загружает config, LLM provider, tools, agents.
3. На каждом `ProjectState` оркестратор ставит `TaskSpec` нужным ролям.
4. Агент вызывает LLM (structured JSON) и разрешённые tools.
5. Claims/hypotheses/reports пишутся в память проекта.
6. Verification / Red Team получают **артефакты claims**, не чужие chat-транскрипты.
7. `WorkflowEngine` выбирает следующее состояние (в т.ч. назад на итерацию).
8. Decision Log фиксирует «почему так решили».

## Независимая проверка

```
Claim artifacts ──► VerificationAgent
                 └──► RedTeamAgent
                        └──► ChiefEngineer (synthesis)
```

Не каскад «все согласились».

## MVP vs extension points

**MVP:** 6 агентов, mock+cursor LLM, subprocess sandbox, research stub, spider silk scaffold, CLI run.

**Позже (точки расширения, без кода в MVP):**

- Engineering Designer / Experimental Scientist (enum уже есть)
- Real web/patent research backend за `research.query`
- Docker / cgroup sandbox вместо subprocess
- OpenAI-compatible provider (достаточно нового класса под `LLMProvider`)
- FEM/CFD/CAD/Jupyter tools в `tools/`
- OpenTelemetry exporter вместо/поверх JSONL sink
- Cloud Cursor agents как workers (не как замена orchestrator)

## Пример прогона (mock)

```bash
python -m ai_lab run projects/spider_silk_industrial --provider mock
```

Ожидаемое поведение:

1. UNDERSTANDING / DECOMPOSITION — Chief Engineer
2. RESEARCH → hypotheses/analysis → simulation (реальный python.execute)
3. Первая VERIFICATION → `DISPUTED` → `ITERATION_REQUIRED`
4. Повторный цикл → VERIFICATION `PASS` → RED_TEAM (MEDIUM attacks)
5. SYNTHESIS → `final_report.md` → COMPLETED

Артефакты: `research/`, `calculations/`, `simulations/`, `reviews/`, `decisions/decision_log.jsonl`, `.runs/<run_id>.jsonl`.

## Safety

- Агенты не получают произвольный shell.
- Python: AST whitelist imports, banned `eval/exec/open`, timeout, isolated `-I`, урезанный env.
- Files/artifacts только внутри project root.

## Human in the loop

HITL срабатывает на критическом red team (или явном `HitlRequest`), не на каждом шаге. Тесты могут `--auto-approve-hitl`.
