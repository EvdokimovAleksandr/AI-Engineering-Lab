# Laboratory UI (V2.7)

Local **research workstation** over the existing lab pipeline.

```text
Browser
  → GET /  (laboratory SPA)
  → POST /api/projects | /api/runs  (async by default)
  → ai_lab.ui.service → LabRuntime
  → .runs/<run_id>/ + observability JSONL
  → GET /api/runs/<id>/status|result|events|stream (SSE)
```

This is **not** a second orchestrator, not a chatbot, not React/Next, not auth, not cloud.

## CLI

```bash
python -m ai_lab ui --host 127.0.0.1 --port 8765
python -m ai_lab ui --demo
```

`--demo` seeds `projects/demo_simple_heater` from the `simple_heater` benchmark and prefills the prompt. Provider/routing still come from trusted config (typically `mock` for demos).

Existing CLI is unchanged: `run`, `plan`, `research`, `routing`, `sandbox`, `benchmark`.

## Product flow

1. User enters one natural-language problem.
2. API creates a **Project** + **Run** (or attaches to an existing project).
3. HTTP returns `{run_id, status: RUNNING}` immediately.
4. LabRuntime continues in a background job; UI observes via SSE + status poll.
5. Final page renders a **structured report** (adjudication / evidence / claims). LLM narrative cannot override engineering outcome.

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` `/projects/<id>` `/runs/<id>` | SPA shell (stable URLs) |
| GET | `/api/projects` | Project list with run counts |
| POST | `/api/projects` | Create project (`problem` optional fields: domain, depth, constraints) |
| GET | `/api/projects/<id>` | Project + run history |
| POST | `/api/projects/<id>/runs` | Start run (async default) |
| POST | `/api/runs` | Create run; auto-creates project if needed; `wait:false` on HTTP |
| GET | `/api/runs` | Recent runs |
| GET | `/api/runs/<run_id>` | Manifest + summary + status + result |
| GET | `/api/runs/<run_id>/status` | Pipeline from real TaskGraph + lifecycle |
| GET | `/api/runs/<run_id>/result` | Structured report DTO |
| GET | `/api/runs/<run_id>/events` | Durable JSONL events |
| GET | `/api/runs/<run_id>/stream` | SSE (replay + live) |
| GET | `/api/runs/<run_id>/export?format=markdown\|json` | Export |

`problem` is `UNTRUSTED_DATA`. Stored under `.runs/<id>/inputs/ui_problem.md`. Does **not** overwrite trusted config.

Programmatic `create_run(..., wait=True)` remains synchronous (tests / tools).

## Security

The UI cannot set sandbox / Docker / routing / provider / API keys / budget / HITL auto-approve / solver imports. Unknown privilege keys are rejected.

## Events

Runtime emits durable lifecycle messages into `RunEventSink` (same JSONL as before), including:

`run.created`, `pipeline.ready`, `stage.*`, `task.*`, `run.completed`, `run.failed`.

Stages come from the validated TaskGraph — not a fake timer.

## HITL

If the pipeline returns `AWAITING_HUMAN`, status includes `hitl_required: true`. Resume remains `python -m ai_lab run … --resume`.

## Provenance

UI runs use the same `RunManifest`, TaskGraph, routing snapshot, sandbox snapshot, Evidence Graph, and grounded synthesis as CLI runs.
