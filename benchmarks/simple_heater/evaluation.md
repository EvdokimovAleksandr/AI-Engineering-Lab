# Evaluation — simple_heater

## Workflow

| Observation | Verdict |
|-------------|---------|
| Router → SIMPLE | PASS |
| Router → STANDARD (still correct calc, overbuilt) | PARTIAL |
| Router → RESEARCH / full lab | FAIL |

## Categories

- **correctness** — order of magnitude ~3–3.5 kW; do not hardcode a single golden number.
- **workflow_selection** — see table above.
- **deterministic_validation** — `deterministic_verify` present; claims with units.
- **verification** — NOT_APPLICABLE for SIMPLE (no independent V agent required).
- **resource_efficiency** — must exclude `research`, `red_team`, `hypothesis`.
- **traceability** — `task_routing.json` + manifest under `.runs/<run_id>/`.

## Physics reference (non-authoritative)

Example with \(\rho=1000\,\mathrm{kg/m^3}\), \(c=4180\,\mathrm{J/(kg\cdot K)}\),
losses as \(P = (Q/t)/(1-0.15)\):

\( Q = 20 \times 4180 \times 60 \approx 5.016\,\mathrm{MJ}\),
\( t = 1800\,\mathrm{s}\),
\( P_\mathrm{ideal} \approx 2.79\,\mathrm{kW}\),
\( P_\mathrm{with\ losses} \approx 3.28\,\mathrm{kW}\).

Alternative loss model \(P=(Q/t)\times 1.15\) yields ~3.2 kW. Both are acceptable
orders of magnitude when assumptions are explicit.
