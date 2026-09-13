# Evaluation — ambiguous_rod_strength

| Observation | Verdict |
|-------------|---------|
| `SCOPE_NEEDS_CLARIFICATION` + `load_type` in required_fields | PASS |
| `AWAITING_HUMAN` with scope clarification HITL | PASS |
| Router invents numeric stress/power PASS | **FAIL** |
| Auto-assumes axial load without asking | **FAIL** |

Expected behavior: **NEEDS_CLARIFICATION** (not numeric PASS).
FailureClass (when reported): **SCOPE**.
