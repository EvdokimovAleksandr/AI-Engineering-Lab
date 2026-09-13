# Engineering verification (V2.2)

Deterministic quantitative checks. **LLM proposes → this layer verifies → policy decides.**

The VerificationAgent may interpret a `VerificationResult`. It must not rewrite `CheckStatus`.

See also: [architecture-v2-implementation.md](architecture-v2-implementation.md), [architecture-v2.md](architecture-v2.md).

## Pipeline

```text
Claim
 ↓
VerificationSpec
 ↓
DeterministicVerifier
 ├── Units          (Pint registry — no string-equality tables)
 ├── Computation    (AST interpreter; no eval/exec)
 ├── Tolerance      (explicit absolute and/or relative)
 ├── Bounds         (inclusive min/max)
 └── Sanity         (named boolean extension point, not a physics engine)
 ↓
VerificationResult
 ↓
VerificationAgent   (interpret only; may use a different routed model)
 ↓
Adjudication        (deterministic FAIL cannot become PASS; empty checks ≠ PASS;
                     missing required evidence → INSUFFICIENT_EVIDENCE;
                     optional model id is provenance only)
```

V2.6 adds a **CalculationSpec** contract and evidence-completeness gate before adjudication.
See [benchmark-integrity.md](benchmark-integrity.md).

## Dimension vs Pint (PR-02)

Contract identity is semantic `Dimension` (`length`, `mass`, `area`, `pressure`, …) in
`ai_lab.checks.units`. Mapping: **Dimension → reference SI unit → Pint dimensionality**.

| Token in `expected_dimensions` | Meaning |
|---|---|
| `length`, `area`, `power`, … | `Dimension` enum — compare via reference unit (`m`, `m**2`, `W`) |
| `m`, `Pa`, `W`, `liter` | Real Pint units |
| `L`, `L**2`, `M`, `T` | Legacy SI base letters (Chief) → LENGTH / AREA / MASS / TIME **before** Pint |

**Choice:** interpret legacy letters as Dimension (fixes false FAIL `expected L, got m`).
Do **not** pass expected `L` through `parse_quantity` — Pint would treat it as litre.
On *actual* `declared_outputs`, bare `L` remains litre.

## Numerical policy

- IEEE-754 **float64**
- Pint converts `actual` into `expected.units` before subtraction
- `PASS` iff `abs(actual - expected) <= absolute + relative * abs(expected)`
- **No implicit epsilon.** Missing tolerance is invalid input (the spec model requires `ToleranceSpec`)
- Python `**` is right-associative; `|exponent|` is capped at 1000
- Identical `VerificationSpec` → identical `CheckStatus` and normalized magnitudes

## Statuses (`CheckStatus`)

Distinct from agent-level `VerificationStatus` (`PASS|FAIL|DISPUTED|INSUFFICIENT_EVIDENCE`).

| Status | Meaning |
|--------|---------|
| `PASS` | Within tolerance and bounds |
| `FAIL` | Numeric mismatch or failed sanity check |
| `INCOMPATIBLE_DIMENSIONS` | e.g. MPa vs N — not a generic FAIL |
| `OUT_OF_BOUNDS` | Outside `BoundsSpec` |
| `INVALID_INPUT` | Bad units, forbidden AST, oversize expression |
| `EVALUATION_ERROR` | e.g. division by zero |
| `TIMEOUT` | `max_computation_seconds` exceeded |

## Limits

Configured like research limits (`config/default.yaml` → `verification.limits`), **not** a second `RunBudget`:

- `max_computation_seconds`
- `max_expression_chars`
- `max_ast_nodes`
- `max_quantities`

Sandbox `python.execute` remains the path for agent-authored recompute snippets and still counts toward `RunBudget.tool_calls`. Expression verification is in-process (`SafeExpressionEvaluator`). The sandbox is process-isolated compute with a killable worker — not a replacement for this verifier. See [compute-sandbox.md](compute-sandbox.md).

## Provenance

`VerificationResult.provenance` records verifier version, spec hash, inputs, units, formula, tolerance, bounds, and this numerical policy.

On disk: `.runs/<run_id>/reviews/checks/<result_id>.json`.

On the existing evidence graph: `GraphNodeType.CHECK` with `TESTS` / `VERIFIED_BY` to `CLAIM`. No new store.

## Legacy MathCheck

`MathCheckRequest` still works. Expressions are adapted into a dimensionless `VerificationSpec`. `required_units` stays **string-tag** equality for backward compatibility. New unit-aware claims should embed `Claim.verification_spec`.

## Compute sandbox vs this layer

`python.execute` / `ComputeSandbox` (local subprocess or Docker) is a **different path**. Docker isolation does not replace Pint/AST `CheckStatus`. Timeout or `MEMORY_LIMIT` is `SandboxStatus`, not `VerificationStatus.FAIL`. See [compute-sandbox.md](compute-sandbox.md) and [docker-sandbox.md](docker-sandbox.md).
