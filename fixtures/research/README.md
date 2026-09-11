# Research search/replay fixtures

JSON files consumed by `ReplaySearchProvider` when `research.backend=replay`.

Each file:

```json
{
  "query": "exact query string",
  "hits": [{ "uri": "https://...", "title": "...", "publisher": "...", "snippet": "..." }],
  "sources": [{ "uri": "https://...", "content": "retrieved body" }]
}
```

CI must not require the network: `content` is the retrieved body used by SourceResolver.
