# Spider silk industrial production — research orchestration

## Problem statement

Исследовать современные подходы к промышленному производству spider silk и определить:

1. какие production platforms реально используются;
2. какие approaches наиболее перспективны;
3. главные технологические bottlenecks;
4. какие свойства натурального spider silk сложнее всего воспроизвести;
5. какие методы уже демонстрировали масштабирование;
6. какие ограничения остаются нерешёнными.

## IMPORTANT — MOCK research backend

На текущем этапе research backend **может быть MOCK**. Этот benchmark
**не** притворяется настоящим научным исследованием.

Цель сейчас: проверить правильность **orchestration**

- decomposition;
- competing approaches / hypotheses;
- evidence structure;
- contradiction handling;
- uncertainty;
- explicit missing evidence.

**STUB / empty research ≠ engineering PASS** (expected_behavior:
`ORCHESTRATION_ONLY`). Synthetic tensile fixtures remain STUB, not FACT.

### Spider silk 2.0 scaffold

Сфокусированная фаза (без полного industrial pipeline):
[`phases/00_problem_definition/`](phases/00_problem_definition/) —
главный cost bottleneck при заданной system boundary.

Позже benchmark будет повторён на реальных источниках.
