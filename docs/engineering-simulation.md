# Engineering Simulation Framework (V2.5)

Structured engineering models. Python/Docker remain **execution mechanisms**, not the scientific abstraction.

```text
Problem
  → TaskGraph
  → EngineeringModel / SimulationSpec
  → deterministic validate
  → SolverRegistry → EngineeringSolver
  → ComputeSandbox (only if the solver needs untrusted Python)
  → SimulationResult
  → DeterministicVerifier
  → Verification ∥ Red Team
  → Adjudication
  → Evidence Graph
```

See also: [engineering-verification.md](engineering-verification.md), [compute-sandbox.md](compute-sandbox.md), [taskgraph-planner.md](taskgraph-planner.md), [ui.md](ui.md).

## What this is (and is not)

An `EngineeringSolver` is a **deterministic computation**. It is not an agent and not a new `AgentRole`.

`SimulationStatus` is not `CheckStatus`, `TaskStatus`, or `SandboxStatus`. A solver error means the simulation did not produce a trustworthy result — not that a physical hypothesis is false.

Deterministic math `PASS` is **not** real-world physics `VALID`. Those axes are stored separately on `ScientificStatus`:

| Axis | Meaning |
|------|---------|
| Model validity | Structured spec passed validation |
| Numerical correctness | Convergence / recomputation |
| Physical validity | Domain / empirical status (`UNASSESSED` unless a domain check fires) |
| Evidence confidence | STUB / INPUT_UNVERIFIED / … |

Uncertainty quantification is **not implemented**. Specs carry `uncertainty.supported: false`.

## SimulationSpec

`src/ai_lab/simulation/models.py`. Parameters are existing `Quantity` values (Pint at validation/solve time). Equations are named assignments evaluated by `SafeExpressionEvaluator` (no second evaluator, no `eval`/`exec`).

Assumptions are `EvidenceKind.ASSUMPTION` and cannot be labeled `FACT`. Boundary conditions are named `Quantity` constraints.

Solver identity is a registry key (`uniaxial_tension`). Spec metadata cannot carry Docker flags, host paths, network, API keys, or budget overrides.

## First solver: UniaxialTensionSolver

In-process algebraic solver (`sandbox_backend=in_process` on `ComputationArtifact`). Docker is not used for `F/A`.

```text
A = π d² / 4
σ = F / A
ε = ΔL / L
σ_elastic = E ε
failure_margin = tensile_strength / σ
mass = ρ A L
```

Required explicit assumptions: `linear_elasticity`, `circular_cross_section`, `small_strain`, `isotropic`, `uniaxial`. The solver will not silently assume a material model.

`|σ − Eε|` is compared with the existing tolerance policy (`atol + rtol*|expected|` after Pint conversion). Discrepancies are recorded, not hidden.

## TaskGraph

Runtime-owned kinds (no `AgentRole`):

- `model_build` → trusted fixture `spec_id` (e.g. `uniaxial_tension`)
- `simulation` → `EngineeringSolver`
- `simulation_verification` → `DeterministicVerifier` on outputs

Default `StaticPlanner` pipeline is unchanged (regression). Set `simulation.pipeline: uniaxial_tension` for the tensile benchmark graph.

Planner/LLM may **name** `solver_id` / `spec_id` only if they are in the trusted registry. Arbitrary imports, host paths, and Docker args remain forbidden proposal keys.

## Spider silk benchmark

Fixture: `fixtures/simulation/uniaxial_tension.json`

- `source = fixture://synthetic`
- `trust = STUB`
- Not scientific FACT about spider silk

Hand check: `d = 5 μm`, `ε = 0.01`, `E = 5 GPa` → `σ = Eε = 50 MPa`; `F` is chosen so `F/A` matches.

## Sandbox policy

| Solver | Backend |
|--------|---------|
| UniaxialTensionSolver | in-process + existing artifact hashes |
| Future FEA/CFD with untrusted Python | `ComputeSpec` → Local or Docker sandbox |

Do not send elementary algebra through Docker for architectural symmetry.
