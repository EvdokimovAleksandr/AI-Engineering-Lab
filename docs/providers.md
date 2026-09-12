# LLM Providers

AI Engineering Lab treats every vendor backend as a swappable `LLMProvider`.
**Agents depend on the protocol — never on Cursor, OpenAI, or Anthropic SDKs.**

```text
Agent
  ↓
LLMProvider  (Protocol)  /  LLMRouter (role → ModelConfig)
  ↓
Cursor SDK  |  Mock  |  Replay  |  (future: OpenAI / Anthropic / Google / local)
```

```text
Agent
 ↓
LLMProviderFactory  (registry.create_provider)
 ↓
configured provider
 ↓
model (from RoutingPolicy / YAML)
```

## Architecture

| Layer | Responsibility |
|-------|----------------|
| `agents/*` | Reasoning via `ctx.llm.complete(...)` — no vendor imports |
| `llm/router.py` | Role → `ModelConfig` → registered provider instance |
| `llm/registry.py` | Factory: `mock` / `cursor_sdk` / `replay` (+ future ids) |
| `llm/cursor_sdk.py` | One backend: Cursor `Agent.prompt`, reasoning-only cwd |
| `llm/mock.py` | Deterministic offline backend (no API key) |
| `ToolRegistry` | All project FS / compute side effects |

Cursor must **not** receive the lab project directory as `cwd`. The Cursor provider
uses an empty temporary directory (`ai_lab_cursor_ro_*`) so filesystem edits cannot
bypass `ToolRegistry`.

## `LLMProvider` interface

Defined in `src/ai_lab/core/protocols.py`:

- `name: str`
- `async def complete(self, request: LLMRequest) -> LLMResponse`

`LLMResponse` may include `usage`, `input_tokens`, `output_tokens`, `estimated_cost`.
If the backend does not report usage, those fields stay `None` / empty (`UNKNOWN`).
The lab never invents token counts or cost.

## Cursor provider (`cursor_sdk`)

- Optional dependency: `pip install -e ".[cursor]"` → package `cursor-sdk`
- Credential: env `CURSOR_API_KEY` (see `.env.example`)
- Default model id: `composer-2.5` (overridable via config / request)
- Errors mapped to: `AuthenticationError`, `RateLimitError`, `ProviderUnavailable`,
  `Timeout`, `MalformedResponse` (`src/ai_lab/llm/errors.py`)
- Bounded retry **only** for transient / rate-limit failures — **never** for auth

## Configuration

Top-level switch (and CLI `--provider`):

```yaml
provider: mock          # offline
# provider: cursor_sdk  # real Cursor reasoning
```

Per-role routing (provider-neutral identifiers):

```yaml
routing:
  version: "1"
  roles:
    chief_engineer:
      model: composer-2.5
      # provider: cursor_sdk   # optional; inherits top-level `provider`
    verification:
      model: composer-2.5
```

Future multi-vendor (Agent classes unchanged — only register a new factory):

```yaml
# Conceptual — openai/anthropic factories are not implemented yet
routing:
  roles:
    chief_engineer: { provider: cursor_sdk, model: composer-2.5 }
    theorist:       { provider: openai,     model: gpt-5 }
    verification:   { provider: anthropic,  model: claude-sonnet }
```

CLI override remaps every role’s provider while keeping model ids:

```bash
python -m ai_lab run projects/spider_silk_industrial --provider cursor_sdk
python -m ai_lab run projects/spider_silk_industrial --provider mock
```

## API key setup

1. Create a key: [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations)
2. `cp .env.example .env` and set `CURSOR_API_KEY=` (never commit `.env`)
3. `pip install -e ".[cursor]"` **inside the project `.venv`**
4. Check presence **without printing the secret**:

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('set' if os.getenv('CURSOR_API_KEY') else 'missing')"
```

5. Confirm Git ignores the secret file:

```bash
git check-ignore -v .env
# expected: .gitignore:1:.env    .env
```

### Windows + local proxy (Clash / V2Ray)

If `provider test` fails with **HTTP 503** or **Network request failed**:

1. Your system proxy is intercepting `127.0.0.1` (the Cursor local bridge).
2. Add `127.0.0.1` and `localhost` to the proxy **bypass / skip list**.
3. Keep the proxy for normal internet (Cursor API may need it).
4. Always use `source .venv/Scripts/activate` so `cursor-sdk` is not installed into Store Python (long-path errors).

The lab provider already:
- uses the **async** SDK (avoids WinError 10038 on Windows);
- forces the Python→bridge HTTP client to **bypass proxies**;
- propagates the OS proxy into the bridge env for outbound API calls.

Do **not** put the key in YAML config, test fixtures, or logs.

## Smoke test

```bash
# Real Cursor (requires key + extras)
python -m ai_lab provider test --provider cursor_sdk

# Offline
python -m ai_lab provider test --provider mock
```

Optional pytest contract (not in default `pytest`; needs key + SDK):

```bash
pytest -m cursor
```

The command:

1. Checks `CURSOR_API_KEY` when testing `cursor_sdk` (friendly instructions if missing)
2. Builds a minimal provider via the factory
3. Sends a tiny structured JSON prompt
4. Writes a **sanitized** artifact under `.runs/provider_test_*.json` (no secrets)

Schema-drift regressions (assumptions as objects, `units` as string, iteration graph prune)
live under `tests/fixtures/llm_schema_drift/` and `tests/test_llm_schema_drift.py`.
These run in normal `pytest` without a live Cursor key.

## Lab run through Cursor

Benchmark (isolated workspace — preferred for `simple_heater`):

```bash
python -m ai_lab benchmark run simple_heater --provider cursor_sdk
```

Full project demo:

```bash
python -m ai_lab run projects/spider_silk_industrial --provider cursor_sdk
```

Deterministic calculation still goes through `python.execute` / `DeterministicVerifier` —
the LLM must not replace math checks.

## Mock provider

- No API key, no network
- Deterministic JSON by `agent_role`
- Default for CI and local development: `provider: mock` or `--provider mock`

## Future providers

To add OpenAI / Anthropic / local models later:

1. Implement a class matching `LLMProvider` in `src/ai_lab/llm/<name>.py`
2. Register a factory in `llm/registry.py` (`_PROVIDER_FACTORIES`)
3. Add the id to `KNOWN_PROVIDER_IDS` and `LabConfig` / CLI choices
4. Point roles at it via `routing.roles.*.provider`

**Do not** import the vendor SDK from `agents/` or branch on vendor names inside Agent code.

## Logging

Safe to log: provider id, model id, request/run id, duration, token usage (if reported), errors.

Never log: `CURSOR_API_KEY`, `Authorization` headers, or raw secret-bearing payloads.
`redact_secrets()` strips key/token/secret-like dict keys before provenance artifacts.
