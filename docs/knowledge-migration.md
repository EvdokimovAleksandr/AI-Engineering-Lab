# Knowledge schema migration (v1 → v2.1)

## What changed

| v1 (flat) | v2.1 |
|-----------|------|
| `research/claims_index.json` + files under `research/` | `.runs/<run_id>/claims/<id>_vN.json` |
| Global claim soup visible to every agent | Visibility: CURRENT_RUN / PROJECT_HISTORY / APPROVED_KNOWLEDGE |
| Optional weak graph in `decisions/evidence_graph.json` | `knowledge/graph/{nodes,edges}.json` with integrity checks |
| No ApprovedKnowledge | `knowledge/approved/` |
| No Conflict records | `knowledge/conflicts/` |

Schema marker: `knowledge/schema_version.json` → `"2.1"`.

## Automatic migration

`KnowledgeService(..., auto_migrate=True)` (default in LabRuntime) calls:

```python
from ai_lab.knowledge.migration import migrate_project_knowledge, needs_migration
```

Behavior:

1. Detects legacy `research/claims_index.json` when schema ≠ 2.1  
2. **Copies** claims into `.runs/run_legacy_migrated/claims/` with `lifecycle=ARCHIVED`  
3. **Leaves originals in place** (non-destructive)  
4. Writes `knowledge/migration_report.json` with warnings  
5. Creates empty graph files if missing  

If a file is missing from the legacy index, a warning is recorded — migration does not abort the lab.

## Manual migration

```python
from ai_lab.memory.project_store import ProjectStore
from ai_lab.knowledge.migration import migrate_project_knowledge

store = ProjectStore.open(Path("projects"), "spider_silk_industrial")
report = migrate_project_knowledge(store)
print(report)
```

## Warnings / limitations

- Legacy claims are **not** automatically promoted to ApprovedKnowledge.  
- Old `decisions/evidence_graph.json` is not auto-imported into `knowledge/graph/` (avoid corrupting integrity); new runs write to the new graph.  
- Re-running migration skips existing destination files and appends warnings.  
- Cross-run claim IDs that collide are last-write-wins in the global index — prefer unique claim_ids.

## Rollback

Original `research/` files remain. Delete `knowledge/` and `.runs/run_legacy_migrated/` only if you intentionally discard the migrated view (not recommended).
