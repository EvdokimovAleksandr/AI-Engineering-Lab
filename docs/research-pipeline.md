# Research pipeline (V2.2)

Real `ResearchProvider` + source provenance. Search backends are replaceable; mock/replay stay offline.

See also: [knowledge-architecture.md](knowledge-architecture.md).

## Flow

```text
Research Agent
      ↓  tool research.query
ResearchProvider
      ↓
SearchProvider          (mock | replay | web)
      ↓
SourceResolver          (fetch, canonical URI, content hash, SourceTrustTier)
      ↓
Evidence extraction     (paragraphs only; no invented location)
      ↓
Knowledge V2.1          (SOURCE / EVIDENCE / CLAIM nodes + existing edges)
      ↓
Run provenance          (query, retrieved_at, content_hash, run_id)
```

Agents do not call HTTP APIs. Retrieved content is **data**, never instructions.

## Trust

Existing `SourceTrustTier` only: `STUB | SECONDARY | PRIMARY`.

`SourceKind` (`patent`, `encyclopedia`, `blog`, …) is descriptive metadata from the URI/host. It is **not** a second quality score. Body text cannot change trust or identity.

| Example | Kind | Tier |
|---------|------|------|
| `mock://…` | stub | STUB |
| `.edu` / `.gov` / arXiv / USPTO | university / government / scientific_article / patent | PRIMARY |
| Wikipedia | encyclopedia | SECONDARY |
| Medium / unknown https | blog / unknown | SECONDARY |

Manufacturer docs are PRIMARY only via config `research.primary_hosts` — we do not guess vendors.

## Identity

- `source_id` = `src_` + sha256(canonical URI)[:16]
- `content_hash` = sha256(retrieved body) — changes if the page changes
- Evidence `DERIVED_FROM` Source; Evidence `SUPPORTS` Claim; Claim `CITES` Source

## Config

`config/default.yaml` → `research:` (same YAML dict pattern as `sandbox`).

```yaml
research:
  backend: mock   # mock | replay | web
  web:
    engine: duckduckgo  # or brave
    api_key_env: BRAVE_SEARCH_API_KEY
  limits:
    max_queries: 8
    max_sources: 12
    max_content_bytes: 200000
    timeout_seconds: 15
```

`RunBudget` still counts `research.query` as a tool call. Research limits are operational caps (like sandbox timeout), not a second ledger.

Web `brave` without `BRAVE_SEARCH_API_KEY` **fails loud**. Mock/replay need no key. There is no silent fallback from web → mock.

## CLI

```bash
python -m ai_lab research "spider silk spinning"
python -m ai_lab research "spider silk spinning" --research-backend mock
python -m ai_lab research "spider silk spinning" --project projects/spider_silk_industrial
```

Prints JSON: `query`, `sources`, `evidence`, `provenance`. Optional `--project` ingests into Knowledge V2.1.

## Tests / replay

`fixtures/research/*.json` + `ReplaySearchProvider`. Pytest never requires the internet.
