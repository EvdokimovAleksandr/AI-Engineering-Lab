# Compute sandbox (V2.4b / V2.4c)

Process-isolated and (optionally) container-isolated general-purpose Python execution.

```text
TaskGraph
  → ToolRegistry
  → python.execute          # one RunBudget tool call
  → ComputeSpec             # validated; cannot escalate trusted policy
  → ComputeSandbox factory
       ├── LocalSubprocessSandbox   # V2.4b — Windows-first, killable process
       └── DockerSandbox            # V2.4c — optional Docker hard isolation
  → ComputationArtifact
  → Evidence / Verification / Knowledge
```

Docker details, flags, TCB, and the capability table: [docker-sandbox.md](docker-sandbox.md).

Algebraic engineering solvers (V2.5 `UniaxialTensionSolver`) run **in-process** through `SafeExpressionEvaluator` and write the same `ComputationArtifact` store with `sandbox_backend=in_process`. Untrusted Python still goes through this sandbox. See [engineering-simulation.md](engineering-simulation.md).

## Two execution paths (do not unify)

| Path | Trust model | What it is |
|------|-------------|------------|
| `DeterministicVerifier` + `SafeExpressionEvaluator` | Restricted AST, in-process, Pint | Numeric/unit checks. Authoritative `CheckStatus`. |
| `ComputeSandbox` | Untrusted Python inside a **separate process or container** | Agent-authored snippets via `python.execute`. |

Sandbox timeout/spawn failure is `SandboxStatus`, **not** `CheckStatus.FAIL` and not `VerificationStatus.FAIL`. A computation that did not run is not a scientific failure.

Research (`research.query`) keeps its own network semantics. Do not route web research through the compute sandbox.

## Policy hierarchy

```text
Trusted config (config/default.yaml sandbox:)
  → ToolSpec (sandbox_required, network=deny, filesystem=run_scoped)
  → TaskSpec.allowed_tools
  → ComputeSpec (code / inputs / requested limits)
  → Execution (local_subprocess | docker)
```

The LLM may propose **code and inputs**. It must not choose host `cwd`, `shell`, `command`, `docker_args`, `image`, mounts, environment mutation, enable network, or raise limits above the trusted ceiling. Escalation is rejected (no silent clamp).

`RunBudget` remains the only tool-call ledger. The inner worker / container is **not** a second tool call. `fallback_backend: none` — missing Docker does not degrade to local.

## LocalSubprocessSandbox (V2.4b)

Worker: `sys.executable -I sandbox/runner.py --workspace <run-scoped dir>` with `shell=False`. Command line is built from trusted constants. Inputs are a JSON file in the workspace, not string interpolation.

Workspace: `.runs/<run_id>/sandbox/<task_id>/` (read/write there). Project `.git`, `.env`, other runs, and the user profile are not put on the path or in the environment.

Environment: allowlisted keys only (`PATH`, `SYSTEMROOT`, …). Secrets / `*TOKEN*` / `*API_KEY*` / `HOME` / `USERPROFILE` are not inherited.

Lifecycle: spawn → monotonic timer → complete | timeout | output cap | failure → terminate → kill if needed → collect capped stdout/stderr → artifact. No background worker after return.

### Windows guarantees (honest)

| Capability | Enforcement | Mechanism |
|------------|-------------|-----------|
| Wall-clock timeout | **HARD** | Parent wait + `TerminateProcess` / Job `TerminateJobObject` |
| Process kill | **HARD** | Same |
| Process tree kill | **HARD** if Job Object attached, else **UNSUPPORTED** | Job `KILL_ON_JOB_CLOSE` |
| stdout/stderr cap | **HARD** | Parent stops reading and kills the worker |
| Environment isolation | **HARD** | Child env constructed from scratch |
| Filesystem jail | **BEST_EFFORT** | cwd = workspace + runner `open()` guard. `os.open` / ctypes can bypass |
| Network deny | **UNSUPPORTED** | Policy is `deny` (requested). Local process can still open sockets. No fake “isolated” claim |
| Memory limit | **HARD** if Job Objects attached, else **UNSUPPORTED** | `JOB_OBJECT_LIMIT_PROCESS_MEMORY` |
| CPU time | **HARD** if Job Objects attached, else **UNSUPPORTED** | `PerProcessUserTimeLimit` |
| Max processes | **HARD** if Job Objects attached, else **UNSUPPORTED** | `ActiveProcessLimit` |

Job Objects are wrapped in `sandbox/windows_job.py`, not in the orchestrator. If create/assign fails, metadata records `UNSUPPORTED` — we do not pretend limits still apply.

Linux (same local backend): timeout/kill/process-group kill are HARD; `resource.setrlimit` in the runner is HARD when the OS allows it; network remains UNSUPPORTED; filesystem remains BEST_EFFORT.

## DockerSandbox (V2.4c)

Optional backend. Same `ComputeSpec` / `SandboxPolicy` / `SandboxResult` / `ComputationArtifact`. Selecting `backend: docker` without a reachable engine and local image fails loud (`SandboxUnsupportedError` / `DockerImageUnavailable`). Docker is **not** a default dependency and is never auto-installed.

When the daemon is up and required flags exist: network deny, read-only root, workspace bind, memory/CPU/PIDs, capabilities drop are **HARD**. Mutable image tags are weaker than a pinned digest — see [docker-sandbox.md](docker-sandbox.md).

## Reproducibility

`code_hash` is not identity.

```text
computation_hash = sha256(
  code_hash, input_hash, environment_hash, policy_hash, python_version
)
```

`environment_hash` records Python version, platform, known project dependency fingerprint (package version + pyproject text hash when present — **not** a frozen lockfile), and runner script hash. Docker adds **image digest** (or `unknown` → `environment_reproducibility=partial`).

`compare_computations(a, b)` returns `REPRODUCTION_MATCH` | `REPRODUCTION_MISMATCH` | `IDENTITY_DIFFERENT` | `PARTIAL_ENVIRONMENT`. It does not apply numeric tolerance or scientific judgement.

Container identity ≠ scientific numerical reproducibility (BLAS, threads, unseeded RNG).

## Provenance

Each `python.execute` emits a `RunEvent` (`code_hash`, `input_hash`, `computation_hash`, backend, policy version, limits, status, duration; Docker adds image / digest). Host docker argv with raw paths is not stored — only a sanitized command. Artifact JSON stays under `.runs/<run_id>/computations/`. Graph node type remains `CALCULATION` / `SIMULATION` — no second node type.

`RunManifest` stores run-level `sandbox_backend`, `sandbox_policy_version`, `sandbox_capabilities`. Invocation details live on `ComputationArtifact`.

## CLI

```bash
python -m ai_lab sandbox
```

Prints **both** backends, platform, enforcement levels, and Docker discovery (`installed` / `daemon` / `backend` / `reason`). Values are not optimistic.

## Cursor SDK

`python.execute` still goes through `ToolRegistry`. Cursor remains reasoning-only (temp cwd). The SDK does not get a direct host execution API.
