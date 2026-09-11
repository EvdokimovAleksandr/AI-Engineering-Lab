# Docker sandbox (V2.4c)

Second `ComputeSandbox` backend: OS/container isolation via Docker Engine. Same contracts as `LocalSubprocessSandbox`.

```text
TaskGraph
  → ToolRegistry
  → python.execute          # one RunBudget tool call
  → ComputeSpec
  → SandboxPolicy           # trusted ceiling
  → ComputeSandbox factory
       ├── local_subprocess → LocalSubprocessSandbox
       └── docker           → DockerSandbox
            → docker run (argv list, shell=False)
            → python -I /opt/ai-lab/runner.py --workspace /workspace
            → ComputationArtifact
```

Runtime / orchestrator does **not** branch on Docker CLI. Factory selects the backend.

See also: [compute-sandbox.md](compute-sandbox.md).

## What this is (and is not)

DockerSandbox is **container-isolated compute with Docker-enforced network, resource and filesystem boundaries**.

It is **not** “100% secure”. The trusted computing base still includes:

- Docker daemon
- host kernel
- container runtime (runc/containerd/Docker Desktop VM)

Remote Docker daemons, Kubernetes, GPU, Swarm, and automatic `docker pull` of arbitrary images are out of scope.

## Policy hierarchy (unchanged)

```text
Trusted config (config/default.yaml)
  → ToolSpec
  → TaskSpec.allowed_tools
  → ComputeSpec
  → Backend (local | docker)
```

No lower layer may expand rights. `network: allow` against `network: deny` is rejected. Image, mounts, `--privileged`, devices, and host namespaces cannot appear on `ComputeSpec`.

`fallback_backend` is `none`. Requested Docker + missing engine → `SandboxUnsupportedError`. No silent fall-back to local subprocess.

## Image policy

Image comes from trusted YAML only:

```yaml
sandbox:
  backend: docker          # or local_subprocess
  fallback_backend: none
  docker:
    image: python:3.12-slim
    image_digest: null     # pin sha256:<64 hex> when you can
```

- LLM / planner / `ComputeSpec` cannot set `image`, `docker_args`, mounts, or runtime flags.
- Sandbox **never** `docker pull`s on invocation (`--pull=never`). Missing image → `DockerImageUnavailable`.
- A mutable tag (`python:3.12-slim`) is **compatibility mode**. Artifact records resolved local identity (`image_digest` from inspect `.RepoDigests` / `.Id`) when Docker provides it.
- Without a digest, `environment_reproducibility = partial`. `compare_computations` returns `PARTIAL_ENVIRONMENT`, not a strong `REPRODUCTION_MATCH`.

## Command policy

`build_docker_command` is a pure function (list argv, `shell=False`). Typical flags:

| Flag | Why |
|------|-----|
| `--network none` | HARD network deny |
| `--read-only` | read-only container root |
| `--tmpfs /tmp` | writable temp without a writable root |
| `--mount` workspace `rw` + runner `ro` | only run-scoped host dir is writable |
| `--cap-drop ALL` | no extra Linux capabilities |
| `--security-opt no-new-privileges` | no privilege regain |
| `--memory {N}m` | from `ComputeSpec.memory_mb` |
| `--cpus {x}` | from trusted `sandbox.docker.cpus` |
| `--pids-limit {n}` | from `max_processes` |
| `--user 65534:65534` | non-root when `non_root: true` |
| `--rm` | remove container on exit |
| `--pull never` | no surprise image fetch |
| `--init` | reaping inside the container when supported |

Never passed: `--privileged`, `--network host`, `--pid host`, `--ipc host`, `--cap-add`, `--device`, bind of `/`, `C:\`, docker.sock, `seccomp=unconfined`.

The container command is `python -I runner.py --workspace /workspace` — no `sh -c` / PowerShell.

## Mounts

Host:

```text
.runs/<run_id>/sandbox/<task_id>/
```

Container:

```text
/workspace   (rw bind)
/opt/ai-lab/runner.py  (ro bind of the trusted package runner)
/tmp         (tmpfs)
```

Mount validator uses `Path.resolve` + `relative_to` (case-normalized on Windows). String prefix is not enough: `C:\project2` is not inside `C:\project`. `..`, UNC, and symlink/junction escapes are rejected.

## Environment / secrets

Container env is built from scratch (`-e` allowlist: `PYTHON*`, `AI_LAB_SANDBOX`, `TMP*`). Host `OPENAI_API_KEY` / `AI_LAB_TEST_SECRET` / `*TOKEN*` are not passed.

The **docker CLI process** on the host needs a small PATH/SYSTEMROOT/USERPROFILE allowlist to talk to the local daemon. That is not container env. `DOCKER_HOST` that looks like a remote `tcp:`/`ssh:` daemon is rejected.

## Lifecycle

```text
reap stale containers for this run_id
docker run
  → parent monotonic deadline + stdout/stderr caps
  → complete | timeout | output limit | spawn failure
docker kill / rm -f
artifact
```

Resume (`--resume` / HITL) never continues an old container. A new invocation is a new `docker run`. Stale containers for the same hashed `run_id` label are removed first.

## Reproducibility

```text
computation_hash = hash(code, inputs, environment, policy, python_version)
```

For Docker, `environment_hash` includes **image digest** (or `"unknown"`). Wall clock, PID, container name, and nonce are not part of identity.

**Container reproducibility** (same image id + same code + same policy) is not **scientific numerical reproducibility**. BLAS, CPU arch, thread scheduling, and unseeded `random` / `numpy.random` can still differ. Artifact `determinism` defaults to `unknown`; this backend does not patch RNG.

`--cpus` is a quota, not bitwise-deterministic floating point.

## Capability comparison (honest)

| Capability | LocalSubprocess (Windows) | Docker (daemon + flags available) | Enforcement |
|------------|---------------------------|-----------------------------------|-------------|
| timeout | HARD | HARD | parent deadline + kill |
| process kill | HARD | HARD | Terminate / `docker kill` |
| process tree | HARD if Job attached; else UNSUPPORTED | HARD | Job Object / container PID namespace |
| network deny | **UNSUPPORTED** (policy requested) | **HARD** (`--network=none`) | Docker |
| filesystem | BEST_EFFORT (`open()` guard) | HARD (read-only root + bind) | Docker |
| memory | HARD if Job; else UNSUPPORTED | HARD (`--memory`) | Job / cgroup |
| CPU | HARD if Job (CPU time); else UNSUPPORTED | HARD (`--cpus` quota) | Job / cgroup |
| max processes | HARD if Job; else UNSUPPORTED | HARD (`--pids-limit`) | Job / cgroup |
| environment | HARD | HARD | constructed env / `-e` |
| output caps | HARD | HARD | parent pipes |
| job objects | HARD / UNSUPPORTED | UNSUPPORTED | Windows-only local |

If the daemon is down or a required `docker run` flag is missing, Docker capabilities are reported **UNSUPPORTED** — not optimistic HARD.

```bash
python -m ai_lab sandbox
```

prints both backends plus:

```text
Docker:
  installed: yes/no
  daemon: available/unavailable
  backend: available/unavailable
  reason: ...
```

Binary on PATH is not enough; daemon, Linux containers, flags, and (for `backend=docker`) local image are probed. Docker is not auto-installed.

## Tests / CI

- `tests/test_docker_command_builder.py` — always (no engine).
- Availability / policy unit tests in `tests/test_docker_sandbox.py` — always.
- `@pytest.mark.docker` integration tests — **skipped** if engine/image missing (explicit skip, not a fake PASS).
- Offline CI keeps the default `backend: local_subprocess`.

## Threat model (short)

Protects against: outbound network from compute snippets, reading arbitrary host files, inheriting API keys, unbounded fork/memory, leftover containers, CLI injection via user strings in argv.

Does not protect against: Docker daemon compromise, kernel container-escape 0-days, malicious trusted `sandbox.docker.image`, or treating sandbox timeout as scientific `CheckStatus.FAIL`.
