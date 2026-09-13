# Ambiguous rod strength — clarification gate

## Problem statement

Как сделать стержень прочнее?

## Notes

Это **OPEN_ENDED** benchmark (PR-07): лаборатория **не** должна выдавать
числовой PASS / выдуманный stress ratio. Ожидаемое поведение —

1. `problem_kind = OPEN_ENDED`
2. `SCOPE_NEEDS_CLARIFICATION`
3. Required field: **`load_type`** (axial / bending / combined)
4. HITL / `AWAITING_HUMAN` — не silent default

После ответа пользователя (отдельный run) постановка может стать READY;
в этом benchmark'е проверяется именно отказ от фейкового закрытого ответа.
