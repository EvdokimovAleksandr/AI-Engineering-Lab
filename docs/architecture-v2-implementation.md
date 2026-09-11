# Architecture V2 — Implementation Report

**Date:** 2026-09-11  
**Scope:** P0 + P1 from [architecture-audit.md](architecture-audit.md) / [architecture-v2.md](architecture-v2.md)  
**Constraint:** no new LLM agent roles; no K8s/vector DB/distributed infra.

---

## 1. What the audit found (summary)

Critical gaps in MVP: theatrical verification (LLM status without recompute), mock PASS-by-call-count, final report from Chief prose, Cursor FS escape via project cwd, no evidence graph / run manifest / budget, shared full claim dump to reviewers, sequential V→RT coupling.

---

## 2. What was fixed (P0)

| ID | Fix |
|----|-----|
| P0.1 | Docs now describe `STAGE_ROLES` as stage table, not dependency graph |
| P0.2 | `SynthesisBundle` + gated `render_final_report`; LLM polish cannot redefine proof |
| P0.3 | DecisionLog canonical owner = `LabRuntime` only; chief does not append |
| P0.4 | `CursorSDKProvider(reasoning_only=True)` uses empty temp cwd; project cwd ignored |
| P0.5 | `MathCheck` + planted-error tests; verification forced FAIL on critical checks |
| P0.6 | Mock PASS only via fixture (`deterministic_all_passed` / override), never call count |

---

## 3. What was fixed (P1)

| Item | Implementation |
|------|----------------|
| RunManifest | `memory/run_store.py` → `.runs/<run_id>/manifest.json` |
| Immutable computations | `ComputationArtifact` per id; overwrite raises `FileExistsError` |
| RunBudget | `orchestrator/budget.py`; `BUDGET_EXCEEDED` terminal state |
| ReviewBundle | Blind claims (no confidence/agent_id); shared frozen path for V∥RT |
| Deterministic checks | `checks/math_check.py` + runner before review |
| Independent V∥RT | `LabRuntime._run_independent_review` via `asyncio.gather` |
| Adjudication | Deterministic FAIL cannot become PASS |
| Evidence graph | JSON nodes/edges (`memory/evidence_graph.py`) |
| CONSENSUS vs INDEPENDENT_EVIDENCE | `AgreementType` on checks/verification/adjudication |
| Claim versioning | `supersede_claim`, `version`, `supersedes`/`superseded_by` |
| IterationPolicy | Reason → RESEARCH / ANALYSIS / CALCULATION / SIMULATION |
| HITL | `AgentResult.hitl_request` → `AWAITING_HUMAN`; CLI `--resume` |
| Tool taint | `trust_level` + `data_not_instructions` on registry results |
| Source trust | `SourceTrustTier`; `mock://` cannot be FACT |

---

## 4. Pipeline after P0/P1

```
USER → Chief → specialists → tools → artifacts / evidence graph
  → DeterministicChecks → ReviewBundle
  → Verification ∥ Red Team → Adjudication
  → FAIL/DISPUTED/INSUFFICIENT → IterationPolicy
  → PASS → SynthesisBundle → final_report.md (gated)
```

---

## 5. Tests

Full suite: **`pytest` → 33 passed** (was 15).

New coverage includes: planted calc error, mock no PASS-by-count, synthesis gates, DecisionLog single-owner, manifest, immutable artifact, budget stop, blind bundle, V/RT isolation, adjudication hard gate, claim supersede, tool UNTRUSTED, Cursor reasoning-only cwd, resume run_id.

---

## 6. Remaining limitations (explicit)

1. **Task graph** still mostly static `STAGE_ROLES` (Chief `follow_up_tasks` still unused as planner).  
2. **Evidence graph** is JSON file, not a query engine; links are minimal.  
3. **Cursor SDK** isolation depends on SDK honoring temp `cwd` + prompt; not a formal capability sandbox.  
4. **Multi-model anti-collusion**: config can set per-role models, but default still one model family.  
5. **Research** remains stub (`mock://` = STUB).  
6. **HITL resume** restores state/run_id; interactive stdin decision UI not built (auto-approve / pending payload only).  
7. **Record/replay** LLM fixtures folder structure not fully scaffolded (mock provider covers CI).  
8. **Pint** not added — unit checks are simple string equality on tags.  
9. Re-running a COMPLETED project starts a **fresh** UNDERSTANDING run (by design); old claims remain on disk (may accumulate across runs).

---

## 7. Most logical P2 next

1. Run-scoped claim namespaces / purge-or-freeze project claims per run  
2. Real research backend with resolvable PRIMARY/SECONDARY sources  
3. Provider router + cost tracking from real usage  
4. Docker/cgroup sandbox limits  
5. Richer evidence-graph queries for final decision paths  
6. Optional second model mandatory for verification vs author  

**Do not** add more agents until tools + checks + research backends need them.

---

## 8. V2.1 Knowledge system (follow-up)

Implemented run-scoped claim namespaces, ApprovedKnowledge promotion/demotion, Conflict model, Evidence Graph integrity + query API, run comparison, freeze/immutability checks, ResearchProvider interface, record/replay foundation, and non-destructive migration.

Docs: [knowledge-architecture.md](knowledge-architecture.md), [knowledge-migration.md](knowledge-migration.md).

Tests: `tests/knowledge/`, `tests/replay/` — full suite **55 passed**.
