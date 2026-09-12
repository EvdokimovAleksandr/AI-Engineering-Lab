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
| POST | `/api/runs/<run_id>/resume` | Continue the **same** run after HITL (choice/note). 202 |

`problem` is `UNTRUSTED_DATA`. Stored under `.runs/<id>/inputs/ui_problem.md`. Does **not** overwrite trusted config. `original_problem.md` is immutable for the run.

Programmatic `create_run(..., wait=True)` remains synchronous (tests / tools).

## Security

The UI cannot set sandbox / Docker / routing / provider / API keys / budget / HITL auto-approve / solver imports. Unknown privilege keys are rejected.

## Events

Runtime emits durable lifecycle messages into `RunEventSink` (same JSONL as before), including:

`run.created`, `pipeline.ready`, `scope.*`, `research.attempt`, `stage.*`, `task.*`, `run.completed`, `run.failed`.

Stages come from the validated TaskGraph — not a fake timer.

## HITL

If the pipeline returns `AWAITING_HUMAN`, status includes `hitl_required: true` and `hitl` (question, why, options). The live page stays on the run (it does **not** jump to the result page). Resume: `POST /api/runs/{id}/resume` or `python -m ai_lab run … --resume`. Same `run_id` / project.

Scope clarification is not a second questionnaire system: at most `runtime.max_clarification_rounds` (default 2) blocking questions.

Evidence gaps are rendered separately from Assumptions. Empty research shows recovery attempts; provider errors are labeled as provider failure, not “no sources exist”.

## Provenance

UI runs use the same `RunManifest`, TaskGraph, routing snapshot, sandbox snapshot, Evidence Graph, and grounded synthesis as CLI runs.

## V2.7.1 — report truth and run isolation

### Artifact scope

| Scope | Examples |
|-------|----------|
| **Run-scoped (authoritative for a run)** | `.runs/<run_id>/final_report.md`, claims, verification/adjudication/synthesis reviews, computation artifacts, events JSONL, run manifest |
| **Project-scoped** | `problem.md`, `project_meta.json`, run index. Root `final_report.md` is **compatibility-only** (last writer wins) and is never the UI source of truth for `GET /api/runs/<id>/result` |
| **Global/system** | trusted `config/default.yaml`, model registry, sandbox/routing policy |

`build_result_view(run_id)` reads only that run's namespace.

### UI truth hierarchy

```text
1. Deterministic verification / acceptance
2. Accepted claims
3. Structured evidence
4. LLM synthesis
```

```text
truth → evidence → explanation
```

not

```text
LLM explanation → truth
```

- **Quantitative `key_numbers`** come from adjudicated + verified accepted claims. `SynthesisBundle.verified_results` / narrative cannot invent or override them.
- **Engineering status** comes from the run manifest / adjudication, never from synthesis `report_gate` or prose.
- Synthesis remains the explanation layer (executive summary, why, limitations).

### SSE replay

`RunEventSink.subscribe(listener)` atomically snapshots persisted events and attaches the live listener under the same lock as `emit`. Clients receive history up to the subscription point plus every later event, without a replay/subscribe gap and without duplicate `event_id`s.

Reconnect to the same run replays JSONL from disk (no new run). Live listeners are **in-process** (the UI job is a thread). JSONL replay survives UI process restart; in-flight execution does not — there is no durable background worker.

## V2.7.2 — planner reliability

The UI may show **AI planner proposal rejected** and **Deterministic recovery used**. That is expected: the laboratory refused an illegal plan and continued with the TaskRouter static profile. This is a planning event, not an engineering FAIL.

Live + result pages keep two axes:

```text
planner: RECOVERED | ACCEPTED | STATIC | FAILED
engineering: PASS | FAIL | INSUFFICIENT_EVIDENCE | …
```

A recovered run that finishes with PASS is valid. Details (optional) list unsupported proposed roles without remapping them. Raw `TaskGraph invalid: UNKNOWN_ROLE` is not the user-facing outcome when recovery succeeded.

Events: `planner.proposal_rejected`, `planner.recovered` (sanitized diagnostics only — no prompts, keys, or provider secrets).

