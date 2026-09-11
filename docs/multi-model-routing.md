# Multi-model routing (V2.4a)

Deterministic routing of existing `LLMProvider` backends. The router is **not** an agent: it does not run tools, write files, mutate `TaskGraph` / `RunBudget` / `CheckStatus`, or adjudicate.

See also: [architecture.md](architecture.md), [architecture-v2.md](architecture-v2.md), [taskgraph-planner.md](taskgraph-planner.md), [engineering-simulation.md](engineering-simulation.md).

The UI and `SimulationSpec` cannot override `RoutingPolicy`. Solver selection is a trusted registry, not a routed LLM model.

## Flow

```text
TaskGraph
  → RoutingPolicy
  → deterministic validate_routing_policy
  → LLMRouter
  → LLMProvider (mock | cursor_sdk | replay)
  → response + routing provenance
```

```text
Agent  →  LLMRouter  →  RoutingPolicy  →  ModelConfig  →  LLMProvider  →  response
```

## ModelConfig

Serializable runtime identity for one completion:

```text
provider, model, endpoint?, temperature, max_tokens?, model_version?, metadata
```

`provider` and `model` are opaque identifiers (`mock:deterministic`, `cursor_sdk:composer-2.5`). They are not a purchasing catalog.

Canonical hash is SHA-256 of a sorted JSON object of those fields (no secrets). Metadata keys that look like credentials are stripped.

## RoutingPolicy

YAML `routing:` in `config/default.yaml`. Safe CI default: every role uses the top-level `provider` (usually `mock`).

```yaml
routing:
  version: "1"
  roles:
    verification:
      provider: mock
      model: model_b
    red_team:
      provider: mock
      model: model_c
independence:
  require_different_model_from_author: false
  require_different_provider_between_reviewers: false
  require_different_model_between_reviewers: false
```

If `routing.roles` is omitted, the policy is synthesized from `provider` + `models` so older configs still load.

Unknown provider / unknown role / missing required model / independence violations fail **before** any LLM call. The model cannot waive that.

## Independence levels

`classify_independence` is an **architectural** label, not scientific proof.

| Level | Meaning |
|-------|---------|
| `NONE` | Same `(provider, model)` |
| `PARTIAL` | Same provider, different model id |
| `FULL` | Different providers, and (when flags are supplied) blind ReviewBundle + parallel V∥RT |
| `INVALID` | Configured `independence` policy is violated |

```text
Different models  ≠  Independent evidence
```

`AgreementType.INDEPENDENT_EVIDENCE` still means independent supporting evidence or recomputation (deterministic checks). Two LLMs agreeing is `CONSENSUS` at most.

## Independence groups

`TaskSpec.independence_group` is unchanged (V2.3). Verification and Red Team share `independent_review`, stay blind of author workspace, and run in parallel. Routing uses that group as provenance, not as a second grouping mechanism.

## Provenance

Each routed completion records:

```text
role, provider, model, model_version, routing_policy_version,
request_hash, prompt_hash, response_hash, timestamp,
task_id, run_id, latency_ms, input_tokens?, output_tokens?, estimated_cost?
```

Written to the existing run event sink and `.runs/<run_id>/llm/invocations.jsonl`. `RunManifest.routing_policy_version` + `model_routing` snapshot the policy. Pre-V2.4a manifests still load (`routing` fields optional).

Tokens / cost / latency are **observability**. `RunBudget` remains the only hard budget ledger. Missing provider metrics stay `null` — they are never invented.

## Security

- Model selection comes only from the validated `RoutingPolicy`.
- `LLMRequest.model`, `use_model`, `provider` in metadata, and research/LLM prose cannot change routing.
- Planner proposals may not contain `use_model` / `routing_policy` / `model_routing`.
- API keys are never copied into `ModelConfig` or manifests.
- Retries are an extension point (`RetryPolicy`) and are **not** executed. Reroute-on-failure is forbidden so independent reviewers cannot collapse onto one model.

## CLI

```bash
python -m ai_lab routing [--provider mock] [--config path]
```

Prints `role → provider:model`, independence group, pairwise levels, and `VALID` / `INVALID`. No LLM call, no secrets.

## Providers

Factory registry (`mock`, `cursor_sdk`, `replay`) constructs instances. The router only looks up `provider id → instance`. Replay matches fixture model identity so `same routing + same fixture → same response` without a network.

## Not in V2.4a

GPU scheduling, automatic model purchasing, API-key management, economics optimizer, LLM-driven adjudication (adjudication stays deterministic; it may *record* an optional model id).

Compute isolation is V2.4b/V2.4c ([compute-sandbox.md](compute-sandbox.md), [docker-sandbox.md](docker-sandbox.md)), not the router.
