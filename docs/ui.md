# Simple local task UI (V2.5)

Local page to pose an engineering problem in natural language and run the **existing** laboratory pipeline.

```text
Browser
  → GET /  (HTML)
  → POST /api/runs
  → ai_lab.ui.service.create_run
  → LabRuntime (planner → validator → graph → agents/solvers)
  → .runs/<run_id>/  (authoritative files)
  → GET /api/runs/<id>/status|result
```

This is **not** a second orchestrator, not a database, not React/Next, not auth, not cloud.

## CLI

```bash
python -m ai_lab ui --host 127.0.0.1 --port 8765
```

Existing CLI is unchanged: `run`, `plan`, `research`, `routing`, `sandbox`.

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Minimal HTML form |
| GET | `/api/projects` | Directories under `projects/` with `problem.md` |
| POST | `/api/runs` | Body: `{problem, project, action: plan\|run}` |
| GET | `/api/runs/<run_id>` | Manifest + status + result |
| GET | `/api/runs/<run_id>/status` | Stages + HITL flag |
| GET | `/api/runs/<run_id>/result` | Simulation / V / RT / adjudication |

`problem` is `UNTRUSTED_DATA`. It is stored under `.runs/<id>/inputs/ui_problem.md` and passed to `ProblemContext`. It does **not** overwrite `projects/<name>/problem.md`.

## Security

The UI cannot set:

- sandbox / Docker flags / image / network / host path / cwd / command / shell
- routing policy / provider / API keys
- RunBudget / auto_approve_hitl
- solver imports

Unknown JSON fields are rejected. Trusted settings stay in `config/default.yaml`.

## HITL

If the pipeline returns `AWAITING_HUMAN`, status includes `hitl_required: true` and the existing `pending_hitl` payload. Resume remains `python -m ai_lab run … --resume` (no new HITL engine).

## Provenance

UI runs use the same `RunManifest`, TaskGraph, routing snapshot, sandbox snapshot, and Evidence Graph as CLI runs.
