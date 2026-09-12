"""Presentation DTOs for UI — derived from authoritative run artifacts.

Structured truth comes from adjudication / synthesis / claims / checks.
LLM narrative is one field among many and cannot override engineering status.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_lab.memory.project_store import ProjectStore

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


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _run_review(store: ProjectStore, run_id: str, name: str) -> Any | None:
    """Prefer run-scoped review copy; fall back to project-level for older runs."""
    run_path = store.root / ".runs" / run_id / "reviews" / name
    data = _load_json(run_path)
    if data is not None:
        return data
    return _load_json(store.root / "reviews" / name)


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

    # Structured truth wins over narrative.
    engineering_outcome = (
        manifest.get("engineering_outcome")
        or adjudication.get("engineering_outcome")
        or adjudication.get("status")
        or (lifecycle or {}).get("engineering_outcome")
    )
    report_gate = synthesis.get("report_gate") if isinstance(synthesis, dict) else None
    verified_results = list(synthesis.get("verified_results") or []) if isinstance(synthesis, dict) else []
    caveats = list(synthesis.get("caveats") or []) if isinstance(synthesis, dict) else []
    narrative = synthesis.get("narrative") if isinstance(synthesis, dict) else ""
    open_questions = list(synthesis.get("open_questions") or []) if isinstance(synthesis, dict) else []
    residual_risks = list(synthesis.get("residual_risks") or []) if isinstance(synthesis, dict) else []

    claims = _list_claims(store, run_id)
    assumptions = _assumptions_from_claims(claims)
    required_outputs = _required_outputs(understanding, evidence_c, manifest)

    missing = []
    if isinstance(evidence_c, dict):
        missing = list(evidence_c.get("missing_required_outputs") or evidence_c.get("missing") or [])

    confidence = {
        "evidence_status": _evidence_status(engineering_outcome, evidence_c, report_gate),
        "deterministic_verification": _check_status(verification, adjudication),
        "independent_review": _review_badge(red_team, verification),
        "empirical_validation": "NOT_AVAILABLE",
        "simulation_validation": _sim_badge(sim),
        # No invented percentage confidence.
    }

    key_numbers = []
    for item in verified_results:
        if not isinstance(item, dict):
            continue
        key_numbers.append(
            {
                "label": item.get("label") or item.get("name") or item.get("claim_id") or "Result",
                "value": item.get("value") or item.get("statement"),
                "unit": item.get("unit"),
                "verified": True,
                "claim_id": item.get("claim_id"),
            }
        )

    why_chain = _why_chain(understanding, verified_results, synthesis)

    final_report_md = None
    fr = store.root / "final_report.md"
    if fr.is_file():
        final_report_md = fr.read_text(encoding="utf-8")

    payload = {
        "run_id": run_id,
        "project_id": store.name,
        "lifecycle_status": (lifecycle or {}).get("status") or _infer_lifecycle(manifest, snapshot),
        "final_state": manifest.get("final_state") or snapshot.state.value,
        "engineering_outcome": engineering_outcome,
        "report_gate": report_gate,
        "hitl_required": snapshot.state.value == "AWAITING_HUMAN",
        "executive_summary": narrative or _fallback_summary(engineering_outcome, key_numbers, missing),
        "key_numbers": key_numbers,
        "why": why_chain,
        "evidence": {
            "completeness": evidence_c,
            "missing_required_outputs": missing,
            "verified_result_count": len(verified_results),
            "claim_count": len(claims),
        },
        "claims": claims,
        "assumptions": assumptions,
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
        "started_at": manifest.get("started_at") or (lifecycle or {}).get("started_at"),
        "finished_at": manifest.get("finished_at") or (lifecycle or {}).get("finished_at"),
        "model_routing": manifest.get("model_routing"),
        "error": (lifecycle or {}).get("error"),
    }


def export_markdown(result: dict[str, Any]) -> str:
    """Reproducible markdown report from structured result (not a second truth source)."""
    lines = [
        f"# Investigation result — {result.get('run_id')}",
        "",
        f"**Engineering outcome:** {result.get('engineering_outcome') or 'PENDING'}",
        f"**Evidence:** {((result.get('confidence') or {}).get('evidence_status'))}",
        "",
        "## Executive summary",
        "",
        str(result.get("executive_summary") or "_No summary._"),
        "",
    ]
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


def _list_claims(store: ProjectStore, run_id: str) -> list[dict[str, Any]]:
    claims_dir = store.root / ".runs" / run_id / "claims"
    if not claims_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(claims_dir.glob("claim_*_v*.json")):
        data = _load_json(path)
        if isinstance(data, dict):
            out.append(
                {
                    "claim_id": data.get("claim_id"),
                    "statement": data.get("statement"),
                    "kind": data.get("kind"),
                    "source": data.get("source"),
                    "source_trust": data.get("source_trust"),
                    "lifecycle": data.get("lifecycle"),
                    "computation_artifact_id": data.get("computation_artifact_id"),
                    "refs": data.get("refs") or [],
                }
            )
    return out


def _assumptions_from_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for c in claims:
        kind = str(c.get("kind") or "").upper()
        if kind == "ASSUMPTION":
            rows.append({"text": c.get("statement"), "status": "Assumed", "claim_id": c.get("claim_id")})
        elif kind == "FACT":
            rows.append({"text": c.get("statement"), "status": "Given", "claim_id": c.get("claim_id")})
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
    if eng in {"INSUFFICIENT_EVIDENCE", "FAIL", "DISPUTED"}:
        return str(eng)
    if report_gate in {"INSUFFICIENT_EVIDENCE", "INCOMPLETE", "DISPUTED"}:
        return str(report_gate)
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


def _fallback_summary(eng: Any, key_numbers: list[dict[str, Any]], missing: list[Any]) -> str:
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
    if final_s in {"COMPLETED", "DISPUTED", "AWAITING_HUMAN"}:
        return "COMPLETED"
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
