"""Presentation DTOs for UI — derived from authoritative run artifacts.

UI truth hierarchy (never inverted):
  1. Deterministic verification / acceptance
  2. Accepted claims (adjudicated + verified computation provenance)
  3. Structured evidence
  4. LLM synthesis (explanation only)

Synthesis can explain truth. Synthesis cannot define truth.

Artifact scope:
  RUN-SCOPED (authoritative for GET /runs/<id>/result):
    .runs/<run_id>/final_report.md, claims/, reviews/, computations/,
    planner/, manifest, events
  PROJECT-SCOPED (not a run's source of truth):
    problem.md, project_meta.json, run index, legacy root final_report.md
  GLOBAL/SYSTEM:
    trusted config, model registry, sandbox/routing policy
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_lab.core.models import (
    AdjudicationResult,
    Claim,
    DeterministicCheckReport,
    EvidenceCompletenessReport,
)
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.synthesis import validate_synthesis_grounding

logger = get_logger(__name__)

# Keys that must never appear in UI/export payloads.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|docker_host|authorization)",
    re.IGNORECASE,
)


def _scrub(obj: Any) -> Any:
    """Remove secret-looking keys from nested dicts (defense in depth for UI)."""
    if isinstance(obj, dict):
        return {
            k: _scrub(v)
            for k, v in obj.items()
            if not _SECRET_KEY_RE.search(str(k))
        }
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def _planning_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Planner reliability is a separate axis from engineering_outcome."""
    raw = manifest.get("planner") if isinstance(manifest.get("planner"), dict) else {}
    recovered = bool(raw.get("recovered"))
    accepted = bool(raw.get("accepted"))
    requested = raw.get("requested")
    if recovered:
        status = "RECOVERED"
        headline = "AI planner proposal rejected"
        recovery = "Deterministic planner selected."
    elif accepted and requested == "llm":
        status = "ACCEPTED"
        headline = "AI planner proposal accepted"
        recovery = None
    elif accepted:
        status = "STATIC"
        headline = "Deterministic planner selected"
        recovery = None
    elif raw:
        status = "FAILED"
        headline = "Planning failed"
        recovery = None
    else:
        status = None
        headline = None
        recovery = None
    return {
        "status": status,
        "headline": headline,
        "recovery": recovery,
        "requested": requested,
        "accepted": accepted,
        "recovered": recovered,
        "fallback": raw.get("fallback"),
        "fallback_profile": raw.get("fallback_profile"),
        "retry_count": raw.get("retry_count", 0),
        "planner_attempts": raw.get("planner_attempts"),
        "planner_rejections": raw.get("planner_rejections"),
        "planner_fallbacks": raw.get("planner_fallbacks"),
        "final_planner": raw.get("final_planner_type"),
        "reason": raw.get("rejection_reason"),
        "failure_class": raw.get("failure_class"),
        "validation_errors": list(raw.get("validation_errors") or []),
        "user_reason": (
            "The generated plan contained unsupported roles or an invalid schema."
            if recovered
            else None
        ),
    }


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _run_review(store: ProjectStore, run_id: str, name: str) -> Any | None:
    """Load a review artifact for this run only.

    Project-root reviews/ is last-writer-wins compatibility storage and must
    not be used as the source of truth for a specific run (cross-run leak).
    """
    run_path = store.root / ".runs" / run_id / "reviews" / name
    return _load_json(run_path)


def build_pipeline_nodes(
    store: ProjectStore, run_id: str, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pipeline stages from the real TaskGraph + executions — not a fixed fake list."""
    graph = _load_json(store.root / ".runs" / run_id / "planner" / "task_graph.json") or {}
    executions = _load_json(store.root / ".runs" / run_id / "planner" / "executions.json") or []
    exec_by_id = {
        row.get("task_id"): row for row in executions if isinstance(row, dict) and row.get("task_id")
    }
    tasks = graph.get("tasks") or []
    # Preserve first-seen stage order from the graph.
    order: list[str] = []
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        stage = _stage_from_task(task)
        if stage not in by_stage:
            by_stage[stage] = []
            order.append(stage)
        tid = task.get("task_id")
        ex = exec_by_id.get(tid) or {}
        by_stage[stage].append(
            {
                "task_id": tid,
                "kind": task.get("task_kind") or task.get("kind"),
                "role": task.get("role"),
                "status": ex.get("status") or "PENDING",
                "artifact_paths": ex.get("artifact_paths") or [],
            }
        )
    nodes: list[dict[str, Any]] = []
    for stage in order:
        items = by_stage[stage]
        statuses = [t.get("status") for t in items]
        if any(s == "RUNNING" for s in statuses):
            status = "RUNNING"
        elif any(s == "FAILED" for s in statuses):
            status = "FAILED"
        elif statuses and all(s in {"SUCCESS", "SKIPPED"} for s in statuses):
            status = "DONE"
        elif any(s in {"SUCCESS", "SKIPPED"} for s in statuses):
            status = "PARTIAL"
        else:
            status = "PENDING"
        nodes.append(
            {
                "stage": stage,
                "status": status,
                "tasks": items,
                "artifact_count": sum(len(t.get("artifact_paths") or []) for t in items),
            }
        )
    scope = _load_json(store.root / ".runs" / run_id / "planner" / "scope.json")
    if isinstance(scope, dict) and scope.get("status"):
        st = str(scope.get("status") or "")
        if st == "SCOPE_RESOLVED" or st == "SCOPE_ASSUMED":
            scope_status = "DONE"
        elif st == "SCOPE_NEEDS_CLARIFICATION":
            scope_status = "HITL"
        elif st == "SCOPE_UNRESOLVED":
            scope_status = "FAILED"
        else:
            scope_status = "PENDING"
        nodes.insert(
            0,
            {
                "stage": "SCOPE_RESOLUTION",
                "status": scope_status,
                "tasks": [],
                "artifact_count": 0,
            },
        )
    if not nodes and manifest.get("final_state"):
        # Pre-graph / plan-only: show lifecycle from project snapshot if available.
        nodes.append(
            {
                "stage": "PLANNING",
                "status": "DONE" if manifest.get("task_graph_id") else "PENDING",
                "tasks": [],
                "artifact_count": 0,
            }
        )
    return nodes


def _stage_from_task(task: dict[str, Any]) -> str:
    state = task.get("state_context")
    if state:
        return str(state)
    kind = str(task.get("task_kind") or task.get("kind") or "")
    role = str(task.get("role") or "")
    if kind in {"model_build"}:
        return "CALCULATION"
    if kind in {"simulation", "simulation_verification"}:
        return "SIMULATION"
    if kind == "deterministic_check":
        return "VERIFICATION"
    if kind == "adjudication":
        return "ADJUDICATION"
    role_map = {
        "research": "RESEARCH",
        "theorist": "HYPOTHESIS",
        "simulation": "SIMULATION",
        "verification": "VERIFICATION",
        "red_team": "RED_TEAM",
        "chief_engineer": "UNDERSTANDING",
    }
    return role_map.get(role, "PLANNING")


def build_result_view(
    store: ProjectStore,
    run_id: str,
    *,
    manifest: dict[str, Any],
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured report for the result page — synthesis cannot override status."""
    snapshot = store.load_snapshot()
    synthesis = _run_review(store, run_id, "synthesis_bundle.json") or {}
    adjudication = _run_review(store, run_id, "last_adjudication.json") or {}
    verification = _run_review(store, run_id, "last_verification.json")
    red_team = _run_review(store, run_id, "last_red_team.json")
    evidence_c = _run_review(store, run_id, "evidence_completeness.json") or {}
    understanding = _run_review(store, run_id, "chief_understanding.json") or {}
    acceptance = _run_review(store, run_id, "benchmark_acceptance.json")
    sim = _load_json(store.root / ".runs" / run_id / "artifacts" / "simulation_result.json")

    # Structured truth wins over narrative. Never take engineering status from synthesis.
    engineering_outcome = (
        manifest.get("engineering_outcome")
        or adjudication.get("engineering_outcome")
        or adjudication.get("status")
        or (lifecycle or {}).get("engineering_outcome")
    )
    report_gate = synthesis.get("report_gate") if isinstance(synthesis, dict) else None
    caveats = list(synthesis.get("caveats") or []) if isinstance(synthesis, dict) else []
    narrative = synthesis.get("narrative") if isinstance(synthesis, dict) else ""
    open_questions = list(synthesis.get("open_questions") or []) if isinstance(synthesis, dict) else []
    residual_risks = list(synthesis.get("residual_risks") or []) if isinstance(synthesis, dict) else []

    claim_models = _load_run_claim_models(store, run_id)
    claims = [_claim_row(c) for c in claim_models]
    assumptions = _assumptions_from_claims(claims)
    evidence_gaps = _evidence_gaps_from_claims(claims)
    scope = _load_json(store.root / ".runs" / run_id / "planner" / "scope.json") or {}
    research_s = (
        _run_review(store, run_id, "research_sufficiency.json")
        or _load_json(store.root / ".runs" / run_id / "planner" / "research_sufficiency.json")
        or {}
    )
    if isinstance(research_s, dict):
        evidence_gaps = evidence_gaps + list(research_s.get("evidence_gaps") or [])
        # De-duplicate while preserving order.
        seen_g: set[str] = set()
        deduped: list[str] = []
        for g in evidence_gaps:
            key = str(g)
            if key in seen_g:
                continue
            seen_g.add(key)
            deduped.append(key)
        evidence_gaps = deduped
    if isinstance(scope, dict):
        for item in scope.get("assumptions") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            kind = str(item.get("kind") or "")
            if kind == "EVIDENCE_LIMITATION":
                if text not in evidence_gaps:
                    evidence_gaps.append(text)
                continue
            if any(a.get("text") == text for a in assumptions):
                continue
            assumptions.append({"text": text, "status": kind or "Assumed"})
        for item in scope.get("ambiguity") or []:
            text = str(item)
            if text and text not in evidence_gaps and scope.get("status") in {
                "SCOPE_UNRESOLVED",
                "SCOPE_NEEDS_CLARIFICATION",
            }:
                evidence_gaps.append(text)
    required_outputs = _required_outputs(understanding, evidence_c, manifest)
    if isinstance(scope, dict) and scope.get("required_outputs"):
        locked = [{"name": n, "origin": "policy_lock"} for n in scope.get("required_outputs") or []]
        names = {o.get("name") for o in required_outputs}
        required_outputs = locked + [o for o in required_outputs if o.get("name") not in names]

    # Authoritative quantitative results: accepted claims, not SynthesisBundle.verified_results.
    accepted_claims = _accepted_claims_for_run(
        claim_models,
        adjudication=adjudication,
        review_bundle=_run_review(store, run_id, "review_bundle.json"),
    )
    verified_results = [_claim_public_row(c) for c in accepted_claims]
    key_numbers = _key_numbers_from_accepted(accepted_claims)

    missing = []
    if isinstance(evidence_c, dict):
        missing = list(evidence_c.get("missing_required_outputs") or evidence_c.get("missing") or [])

    gates = _engineering_gates(evidence_c)
    confidence = {
        "evidence_status": _evidence_status(engineering_outcome, evidence_c, report_gate),
        "deterministic_verification": _check_status(verification, adjudication),
        "independent_review": _review_badge(red_team, verification),
        "empirical_validation": "NOT_AVAILABLE",
        "simulation_validation": _sim_badge(sim),
        # No invented percentage confidence.
    }

    why_chain = _why_chain(understanding, verified_results, synthesis)

    # Canonical report is run-scoped. Project-root final_report.md is compatibility-only.
    final_report_md = None
    run_report = store.root / ".runs" / run_id / "final_report.md"
    if run_report.is_file():
        final_report_md = run_report.read_text(encoding="utf-8")

    life_status = (lifecycle or {}).get("status") or _infer_lifecycle(manifest, snapshot)
    run_error = _resolve_run_error(lifecycle, manifest)
    # Technical failure is not an engineering result — do not hide it behind synthesis prose.
    if str(life_status).upper() in {"ERROR", "FAILED"}:
        summary = _fallback_summary(
            engineering_outcome, key_numbers, missing, lifecycle_status=life_status, error=run_error
        )
    else:
        summary = narrative or _fallback_summary(engineering_outcome, key_numbers, missing)

    # PR-07: surface MODE so MOCK stub research ≠ LIVE research failure in UI.
    from ai_lab.benchmark.mode import execution_mode_from_provider

    provider = str(manifest.get("model_provider") or "unknown")
    execution_mode = execution_mode_from_provider(provider)

    payload = {
        "run_id": run_id,
        "project_id": store.name,
        "lifecycle_status": life_status,
        "final_state": manifest.get("final_state") or snapshot.state.value,
        "engineering_outcome": engineering_outcome,
        "execution_mode": execution_mode,
        "provider": provider,
        "report_gate": report_gate,
        "hitl_required": snapshot.state.value == "AWAITING_HUMAN",
        "error": run_error,
        "executive_summary": summary,
        "key_numbers": key_numbers,
        "verified_results": verified_results,
        "accepted_claims": verified_results,
        "computation_relevant": gates["computation_relevant"],
        "acceptance_passed": gates["acceptance"],
        "coverage": gates["coverage"],
        "evidence_complete": gates["evidence_complete"],
        "why": why_chain,
        "evidence": {
            "completeness": evidence_c,
            "missing_required_outputs": missing,
            "verified_result_count": len(verified_results),
            "claim_count": len(claims),
            "computation_relevant": gates["computation_relevant"],
            "acceptance": gates["acceptance"],
            "coverage": gates["coverage"],
            "evidence_complete": gates["evidence_complete"],
        },
        "claims": claims,
        "assumptions": assumptions,
        "evidence_gaps": evidence_gaps,
        "scope": _scrub(scope) if scope else None,
        "research": _scrub(research_s) if research_s else None,
        "limitations": residual_risks or caveats,
        "open_questions": open_questions,
        "caveats": caveats,
        "confidence": confidence,
        "required_outputs": required_outputs,
        "policy_lock": {
            "workflow_profile": manifest.get("workflow_profile"),
            "task_routing": manifest.get("task_routing_decision"),
            "calculation_spec_ids": manifest.get("calculation_spec_ids") or [],
        },
        "verification": _scrub(verification) if verification else None,
        "red_team": _scrub(red_team) if red_team else None,
        "adjudication": _scrub(adjudication) if adjudication else None,
        "acceptance": _scrub(acceptance) if acceptance else None,
        "simulation": _scrub(sim) if sim else None,
        "understanding": _scrub(understanding) if understanding else None,
        "synthesis": _scrub(synthesis) if synthesis else None,
        "final_report_markdown": final_report_md,
        "planning": _planning_view(manifest),
        "provenance": {
            "chain": [
                "Problem",
                "Understanding",
                "Calculation/Research",
                "Verification",
                "Evidence",
                "Adjudication",
                "Decision",
            ],
            "links": {
                "task_graph": f".runs/{run_id}/planner/task_graph.json",
                "computations": f".runs/{run_id}/computations/",
                "claims": f".runs/{run_id}/claims/",
                "synthesis": f".runs/{run_id}/reviews/synthesis_bundle.json",
            },
        },
        # Narrative is subordinate — UI must prefer engineering_outcome.
        "narrative_is_authoritative": False,
    }
    return _scrub(payload)


def build_run_summary(
    store: ProjectStore,
    run_id: str,
    *,
    manifest: dict[str, Any],
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact run status for live page and project history."""
    snapshot = store.load_snapshot()
    pipeline = build_pipeline_nodes(store, run_id, manifest)
    active = next((n for n in pipeline if n["status"] == "RUNNING"), None)
    eng = (
        manifest.get("engineering_outcome")
        or (lifecycle or {}).get("engineering_outcome")
        or (snapshot.adjudication_status.value if snapshot.adjudication_status else None)
    )
    problem = _problem_preview(store, run_id)
    evidence_c = _run_review(store, run_id, "evidence_completeness.json") or {}
    verified = 0
    required = 0
    if isinstance(evidence_c, dict):
        required = int(evidence_c.get("required_count") or len(evidence_c.get("required_outputs") or []) or 0)
        verified = int(evidence_c.get("covered_count") or evidence_c.get("verified_count") or 0)
    return {
        "run_id": run_id,
        "project_id": store.name,
        "status": (lifecycle or {}).get("status") or _infer_lifecycle(manifest, snapshot),
        "stage": active["stage"] if active else (pipeline[-1]["stage"] if pipeline else None),
        "engineering_status": eng or "PENDING",
        "final_state": manifest.get("final_state") or snapshot.state.value,
        "required_outputs": required,
        "verified_outputs": verified,
        "evidence_status": _evidence_status(eng, evidence_c, None),
        "pipeline": pipeline,
        "problem_preview": problem,
        "workflow_profile": manifest.get("workflow_profile"),
        "hitl_required": snapshot.state.value == "AWAITING_HUMAN",
        "hitl": snapshot.pending_hitl,
        "started_at": manifest.get("started_at") or (lifecycle or {}).get("started_at"),
        "finished_at": manifest.get("finished_at") or (lifecycle or {}).get("finished_at"),
        "model_routing": manifest.get("model_routing"),
        "error": _resolve_run_error(lifecycle, manifest),
        "planner": _planning_view(manifest),
    }


def export_markdown(result: dict[str, Any]) -> str:
    """Reproducible markdown report from structured result (not a second truth source)."""
    lines = [
        f"# Investigation result — {result.get('run_id')}",
        "",
        f"**Engineering outcome:** {result.get('engineering_outcome') or 'PENDING'}",
        f"**Evidence:** {((result.get('confidence') or {}).get('evidence_status'))}",
        "",
    ]
    planning = result.get("planning") or {}
    if planning.get("status"):
        lines.extend(
            [
                f"**Planning status:** {planning.get('status')}",
                "",
            ]
        )
        if planning.get("recovered"):
            lines.extend(
                [
                    "AI-generated plan violated the TaskGraph schema. "
                    "The laboratory continued with a deterministic plan.",
                    "",
                ]
            )
    lines.extend(
        [
            "## Executive summary",
            "",
            str(result.get("executive_summary") or "_No summary._"),
            "",
        ]
    )
    if result.get("key_numbers"):
        lines.append("## Key numbers")
        lines.append("")
        for kn in result["key_numbers"]:
            unit = f" {kn['unit']}" if kn.get("unit") else ""
            badge = "VERIFIED" if kn.get("verified") else "UNVERIFIED"
            lines.append(f"- **{kn.get('label')}:** {kn.get('value')}{unit} ({badge})")
        lines.append("")
    if result.get("why"):
        lines.append("## Why")
        lines.append("")
        for step in result["why"]:
            lines.append(f"- {step}")
        lines.append("")
    if result.get("assumptions"):
        lines.append("## Assumptions")
        lines.append("")
        for a in result["assumptions"]:
            lines.append(f"- [{a.get('status', 'Assumed')}] {a.get('text')}")
        lines.append("")
    if result.get("limitations"):
        lines.append("## Limitations")
        lines.append("")
        for lim in result["limitations"]:
            lines.append(f"- {lim}")
        lines.append("")
    missing = (result.get("evidence") or {}).get("missing_required_outputs") or []
    if missing:
        lines.append("## Missing evidence")
        lines.append("")
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _load_run_claim_models(store: ProjectStore, run_id: str) -> list[Claim]:
    """Load Claim objects from this run's claims namespace only."""
    claims_dir = store.root / ".runs" / run_id / "claims"
    if not claims_dir.is_dir():
        return []
    out: list[Claim] = []
    for path in sorted(claims_dir.glob("*_v*.json")):
        data = _load_json(path)
        if not isinstance(data, dict):
            logger.error("Unexpected non-object claim file %s", path)
            raise ValueError(f"Claim file is not an object: {path}")
        out.append(Claim.model_validate(data))
    return out


def _claim_row(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "kind": claim.kind.value if claim.kind else None,
        "source": claim.source,
        "source_trust": claim.source_trust.value if claim.source_trust else None,
        "lifecycle": claim.lifecycle.value if claim.lifecycle else None,
        "computation_artifact_id": claim.computation_artifact_id,
        "refs": claim.refs or [],
    }


def _claim_public_row(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "kind": claim.kind.value if claim.kind else None,
        "source": claim.source,
        "source_trust": claim.source_trust.value if claim.source_trust else None,
        "version": claim.version,
        "refs": claim.refs,
        "computation_artifact_id": claim.computation_artifact_id,
    }


def _check_report_from_bundle(review_bundle: Any) -> DeterministicCheckReport | None:
    if not isinstance(review_bundle, dict) or not review_bundle.get("check_report"):
        return None
    return DeterministicCheckReport.model_validate(review_bundle["check_report"])


def _adjudication_model(adjudication: Any) -> AdjudicationResult | None:
    if not isinstance(adjudication, dict) or not adjudication.get("status"):
        return None
    return AdjudicationResult.model_validate(adjudication)


def _accepted_claims_for_run(
    claims: list[Claim],
    *,
    adjudication: Any,
    review_bundle: Any,
) -> list[Claim]:
    """Accepted quantitative claims from adjudication + deterministic provenance.

    SynthesisBundle.verified_results is intentionally ignored here.
    """
    adj = _adjudication_model(adjudication)
    check_report = _check_report_from_bundle(review_bundle)
    accepted, _rejected, _caveats = validate_synthesis_grounding(
        claims=claims,
        check_report=check_report,
        adjudication=adj,
    )
    return accepted


def _key_numbers_from_accepted(claims: list[Claim]) -> list[dict[str, Any]]:
    """UI quantitative cards — only accepted/verified claims, never synthesis prose."""
    numbers: list[dict[str, Any]] = []
    for claim in claims:
        unit = None
        if isinstance(claim.verification_spec, dict):
            unit = claim.verification_spec.get("unit")
        numbers.append(
            {
                "label": claim.claim_id,
                "value": claim.statement,
                "unit": unit,
                "verified": True,
                "accepted": True,
                "claim_id": claim.claim_id,
                "statement": claim.statement,
            }
        )
    return numbers


def _engineering_gates(evidence_c: Any) -> dict[str, bool]:
    if not isinstance(evidence_c, dict) or not evidence_c:
        return {
            "computation_relevant": False,
            "acceptance": False,
            "coverage": False,
            "evidence_complete": False,
        }
    report = EvidenceCompletenessReport.model_validate(evidence_c)
    return {
        "computation_relevant": bool(report.computation_relevant),
        "acceptance": bool(report.acceptance_passed),
        "coverage": bool(report.required_output_coverage),
        "evidence_complete": bool(report.is_complete),
    }


def _list_claims(store: ProjectStore, run_id: str) -> list[dict[str, Any]]:
    return [_claim_row(c) for c in _load_run_claim_models(store, run_id)]


def _assumptions_from_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for c in claims:
        kind = str(c.get("kind") or "").upper()
        if kind == "ASSUMPTION":
            rows.append({"text": c.get("statement"), "status": "Assumed", "claim_id": c.get("claim_id")})
        elif kind == "FACT":
            rows.append({"text": c.get("statement"), "status": "Given", "claim_id": c.get("claim_id")})
    return rows


def _evidence_gaps_from_claims(claims: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for c in claims:
        kind = str(c.get("kind") or "").upper()
        if kind == "EVIDENCE_GAP" and c.get("statement"):
            rows.append(str(c.get("statement")))
    return rows


def _required_outputs(
    understanding: dict[str, Any], evidence_c: dict[str, Any], manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    raw = (
        evidence_c.get("required_outputs")
        or understanding.get("required_outputs")
        or []
    )
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item, "origin": "derived"})
        elif isinstance(item, dict):
            out.append(
                {
                    "name": item.get("name") or item.get("id") or item.get("output"),
                    "origin": item.get("origin") or item.get("source") or "derived",
                    "unit": item.get("unit"),
                    "covered": item.get("covered"),
                }
            )
    for sid in manifest.get("calculation_spec_ids") or []:
        out.append({"name": f"calculation_spec:{sid}", "origin": "policy_lock"})
    return out


def _evidence_status(eng: Any, evidence_c: dict[str, Any], report_gate: Any) -> str:
    # report_gate is synthesis-derived; do not let it override deterministic engineering status.
    _ = report_gate
    if eng in {"INSUFFICIENT_EVIDENCE", "FAIL", "DISPUTED"}:
        return str(eng)
    if isinstance(evidence_c, dict) and evidence_c.get("complete") is False:
        return "PARTIAL"
    if eng in {"PASS", "SUPPORTED"}:
        return "SUPPORTED"
    if eng:
        return str(eng)
    return "PENDING"


def _check_status(verification: Any, adjudication: dict[str, Any]) -> str:
    if isinstance(adjudication, dict) and adjudication.get("status"):
        return str(adjudication.get("status"))
    if isinstance(verification, dict) and verification.get("status"):
        return str(verification.get("status"))
    return "PENDING"


def _review_badge(red_team: Any, verification: Any) -> str:
    parts = []
    if isinstance(verification, dict) and verification.get("status"):
        parts.append(f"verification={verification.get('status')}")
    if isinstance(red_team, dict) and red_team.get("status"):
        parts.append(f"red_team={red_team.get('status')}")
    return ", ".join(parts) if parts else "NOT_RUN"


def _sim_badge(sim: Any) -> str:
    if not isinstance(sim, dict):
        return "NOT_AVAILABLE"
    return str(sim.get("status") or "UNKNOWN")


def _why_chain(
    understanding: dict[str, Any], verified: list[dict[str, Any]], synthesis: dict[str, Any]
) -> list[str]:
    steps: list[str] = []
    for key in ("problem_type", "domain", "approach", "expected_work"):
        val = understanding.get(key)
        if val:
            steps.append(f"{key}: {val}")
    for item in verified[:8]:
        if isinstance(item, dict) and item.get("statement"):
            steps.append(str(item["statement"]))
        elif isinstance(item, dict) and item.get("value") is not None:
            steps.append(f"{item.get('label') or 'value'} = {item.get('value')}")
    for p in synthesis.get("provenance") or []:
        steps.append(str(p))
    return steps


def _resolve_run_error(lifecycle: dict[str, Any] | None, manifest: dict[str, Any]) -> str | None:
    """Surface the real stop reason. Budget stops used to persist error=None."""
    msg = str((lifecycle or {}).get("error") or "").strip()
    if msg:
        return msg
    if manifest.get("final_state") != "BUDGET_EXCEEDED":
        return None
    raw = manifest.get("budget") or {}
    if isinstance(raw, dict) and raw:
        from ai_lab.core.models import RunBudget
        from ai_lab.orchestrator.budget import budget_violation_message

        try:
            budget = RunBudget.model_validate(raw)
        except Exception as exc:
            logger.error("Could not parse manifest.budget for stop reason: %s", exc)
            return "budget exceeded"
        reason = budget_violation_message(budget, include_runtime=False)
        if reason:
            return reason
    return "budget exceeded"


def _fallback_summary(
    eng: Any,
    key_numbers: list[dict[str, Any]],
    missing: list[Any],
    *,
    lifecycle_status: Any | None = None,
    error: Any | None = None,
) -> str:
    life = str(lifecycle_status or "").upper()
    if life in {"ERROR", "FAILED"}:
        msg = str(error or "").strip()
        if msg:
            return f"Investigation did not complete: {msg}"
        return "Investigation failed before an engineering result was produced."
    if eng in {"INSUFFICIENT_EVIDENCE", "FAIL"} or missing:
        miss = ", ".join(str(m) for m in missing) if missing else "required engineering outputs"
        return (
            "The laboratory could not verify the requested result. "
            f"Missing evidence: {miss}."
        )
    if key_numbers:
        parts = []
        for kn in key_numbers[:3]:
            unit = f" {kn['unit']}" if kn.get("unit") else ""
            parts.append(f"{kn.get('label')}: {kn.get('value')}{unit}")
        return "Verified results — " + "; ".join(parts) + "."
    if eng == "PASS":
        return "Investigation completed with a passing engineering adjudication."
    return "Investigation completed. See evidence and adjudication for details."


def _infer_lifecycle(manifest: dict[str, Any], snapshot: Any) -> str:
    final = manifest.get("final_state") or getattr(snapshot, "state", None)
    final_s = final.value if hasattr(final, "value") else final
    if final_s in {"COMPLETED", "DISPUTED"}:
        return "COMPLETED"
    if final_s == "AWAITING_HUMAN":
        return "AWAITING_HUMAN"
    if final_s == "BUDGET_EXCEEDED":
        return "FAILED"
    if final_s == "PLANNED":
        return "PLANNED"
    if manifest.get("started_at") and not manifest.get("finished_at"):
        return "RUNNING"
    return str(final_s or "UNKNOWN")


def _problem_preview(store: ProjectStore, run_id: str) -> str:
    for rel in (
        f".runs/{run_id}/inputs/ui_problem.md",
        f".runs/{run_id}/inputs/problem.md",
        "problem.md",
    ):
        path = store.root / rel
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            return text[:280] + ("…" if len(text) > 280 else "")
    return ""
