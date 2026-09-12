# Benchmarks

Три эталонные задачи для измерения реального поведения лаборатории (routing + orchestration).

## CLI

```bash
python -m ai_lab benchmark list
python -m ai_lab benchmark run simple_heater
python -m ai_lab benchmark run shaft_design
python -m ai_lab benchmark run spider_silk_review
python -m ai_lab benchmark evaluate simple_heater <run_id>
```

Каждый `run` копирует scaffold в изолированный workspace
`benchmarks/.workspace/<id>/` и сохраняет обычный `RunManifest` под
`.runs/<run_id>/`. Claims/evidence **не** смешиваются с другими проектами.

## Benchmark 1 — `simple_heater`

Закрытый расчёт мощности нагревателя (20 L, 20→80°C, 30 min, 15% losses).
Ожидаемый workflow: **SIMPLE**.
Ожидаемый порядок ответа ~3–3.5 kW (не hardcode как oracle).

V2.6: technical `COMPLETED` ≠ engineering `PASS`. Нерелевантный compute (например KV-cache)
или пустая verification → `INSUFFICIENT_EVIDENCE`. См. [benchmark-integrity.md](benchmark-integrity.md).

## Benchmark 2 — `shaft_design`

Предварительный диаметр стального вала (10 kW @ 1500 rpm + factor of safety).
Ожидаемый workflow: **STANDARD**.
Материал — явное assumption, не единственная «правильная» марка.

## Benchmark 3 — `spider_silk_review`

Обзор промышленных подходов к spider silk.
Ожидаемый workflow: **RESEARCH**.
Research backend может быть **MOCK** — оценивается orchestration
(decomposition, competing approaches, evidence structure, contradictions,
uncertainty, missing evidence), не научная истина.

## Evaluation

Модель `EvaluationReport` с категориями:

correctness, workflow_selection, deterministic_validation, verification,
evidence_quality, assumption_quality, traceability, uncertainty_handling,
resource_efficiency.

Вердикты: `PASS` | `PARTIAL` | `FAIL` | `NOT_APPLICABLE`.

Пример: shaft_design → SIMPLE = **FAIL**; → COMPLEX = **PARTIAL**.

См. также: [task-routing.md](task-routing.md).
