# Knowledge Architecture V2.1

Scientific knowledge system for AI Engineering Lab: run-scoped claims, evidence graph, approved knowledge, conflicts, and provenance queries.

See also: [knowledge-migration.md](knowledge-migration.md), [architecture-v2-implementation.md](architecture-v2-implementation.md).

## Data model

```text
Project
 ├── Runs (.runs/<run_id>/)
 │    ├── manifest.json (+ freeze_seal)
 │    ├── claims/<claim_id>_vN.json
 │    ├── computations/
 │    └── reviews/
 ├── Knowledge/
 │    ├── approved/
 │    ├── conflicts/
 │    ├── conclusions/
 │    ├── claims_global_index.json
 │    └── graph/{nodes,edges}.json
 ├── Decisions (decision_log.jsonl + decisions_trace/)
 └── Conclusions
```

### Canonical claim identity

```text
{project_id}/{run_id}/{claim_id}/v{version}
```

Not a filename. Stored as `Claim.canonical_id`.

### Claim lifecycle

`ACTIVE` → `SUPERSEDED` | `REJECTED` | `DISPUTED` | `ARCHIVED` | `DEMOTED`

History is never deleted; use `supersede_claim` / `REFUTES` edges.

### Visibility modes

| Mode | Who (default) | Contents |
|------|----------------|----------|
| `CURRENT_RUN` | Verification, Red Team, Theorist, Simulation | Only this run's claims |
| `PROJECT_HISTORY` | Research | All runs (explicit) |
| `APPROVED_KNOWLEDGE` | Chief (+ merged with CURRENT_RUN) | Promoted entries only |

Nobody gets “load everything” by default.

### ApprovedKnowledge

Promotion gates (deterministic policy — LLM cannot authorize):

1. claim not disputed/rejected/superseded  
2. deterministic critical checks OK  
3. verification PASS  
4. red team completed  
5. adjudication PASS  
6. provenance present  
7. **CONSENSUS alone is insufficient**

Demotion preserves history (`status=DEMOTED`).

### Evidence graph

Nodes: PROJECT, RUN, SOURCE, CLAIM, …, CONFLICT, ADJUDICATION, …  
Edges: SUPPORTS, CONTRADICTS, TESTS, VERIFIED_BY, SUPERSEDES, PART_OF, CREATED_IN, …  

Integrity: no dangling edges, no unknown types, no cross-project links except `SAME_AS`, no bidirectional `SUPERSEDES`, no cycles on acyclic edge types, VERIFICATION↔CLAIM for TESTS/VERIFIED_BY.

### Query API (`EvidenceQueryService`)

- `find_supporting_evidence` / `find_contradicting_evidence`  
- `find_verification_chain` / `find_decision_path` / `find_conclusion_dependencies`  
- `find_claim_history` / `find_run_evidence`  
- `get_project_timeline`  
- `compare_runs(run_a, run_b)`

### Philosophy

```text
Raw LLM output → Structured Proposal → Validation → Evidence → Graph → Policy → Approved Knowledge
```

LLM proposes. Deterministic layer validates. Policy authorizes. Agents never write ApprovedKnowledge or Decision ACCEPTED state without gates.

### Storage

JSON files + indexes (swappable later via `KnowledgeRepository` / `EvidenceRepository` / `RunRepository` protocols). No Neo4j in V2.1.

### ResearchProvider

Interface only (`search` / `fetch` / `resolve_source`). Mock remains STUB-tier.

### Record/replay

`fixtures/llm/{role}/*.json` + `ReplayProvider` for deterministic tests.
