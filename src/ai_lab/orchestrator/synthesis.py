"""Deterministic synthesis bundle + grounded final report rendering.

Quantitative accepted claims must come from verified computation / research —
LLM prose cannot invent accepted engineering numbers.
"""

from __future__ import annotations

from ai_lab.checks.calculation_contract import (
    claim_has_verified_computation_provenance,
    passed_claim_ids_from_report,
)
from ai_lab.core.enums import AdjudicationStatus, EvidenceKind
from ai_lab.core.models import (
    AdjudicationResult,
    Claim,
    DecisionRecord,
    DeterministicCheckReport,
    RedTeamReport,
    SynthesisBundle,
    VerificationReport,
)
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

_QUANTITATIVE_KINDS = {
    EvidenceKind.CALCULATION,
    EvidenceKind.SIMULATION_RESULT,
    EvidenceKind.EXPERIMENT_RESULT,
}


def _claim_public(claim: Claim) -> dict:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "kind": claim.kind.value,
        "source": claim.source,
        "source_trust": claim.source_trust.value if claim.source_trust else None,
        "version": claim.version,
        "refs": claim.refs,
        "computation_artifact_id": claim.computation_artifact_id,
    }


def _is_quantitative(claim: Claim) -> bool:
    if claim.kind in _QUANTITATIVE_KINDS:
        return True
    return bool(claim.math_check or claim.verification_spec)


def validate_synthesis_grounding(
    *,
    claims: list[Claim],
    check_report: DeterministicCheckReport | None,
    adjudication: AdjudicationResult | None,
) -> tuple[list[Claim], list[Claim], list[str]]:
    """Split claims into grounded vs rejected-for-synthesis.

    Returns (accepted, rejected, caveats).
    """
    status = adjudication.status if adjudication else AdjudicationStatus.INSUFFICIENT_EVIDENCE
    passed_ids = passed_claim_ids_from_report(check_report)
    accepted: list[Claim] = []
    rejected: list[Claim] = []
    caveats: list[str] = []

    if status != AdjudicationStatus.PASS:
        return [], list(claims), [f"adjudication={status.value}: no accepted quantitative claims"]

    for claim in claims:
        if claim.superseded_by:
            continue
        if not _is_quantitative(claim):
            # Framing / narrative claims are not engineering accepted results.
            caveats.append(
                f"non-quantitative claim {claim.claim_id} excluded from accepted results"
            )
            continue
        if claim_has_verified_computation_provenance(
            claim, check_report=check_report, passed_claim_ids=passed_ids
        ):
            accepted.append(claim)
        else:
            rejected.append(claim)
            caveats.append(
                f"rejected ungrounded quantitative claim {claim.claim_id} "
                "(missing verified computation provenance)"
            )
            logger.error(
                "Synthesis rejected ungrounded quantitative claim %s", claim.claim_id
            )
    return accepted, rejected, caveats


def build_synthesis_bundle(
    *,
    claims: list[Claim],
    verification: VerificationReport | None,
    red_team: RedTeamReport | None,
    decisions: list[DecisionRecord],
    adjudication: AdjudicationResult | None,
    check_report: DeterministicCheckReport | None = None,
    narrative: str = "",
) -> SynthesisBundle:
    """Classify claims from review state — LLM does not decide what is proven."""
    status = adjudication.status if adjudication else AdjudicationStatus.INSUFFICIENT_EVIDENCE
    gate = status.value

    active_claims = [c for c in claims if not c.superseded_by]
    accepted_pubs: list[dict] = []
    rejected_pubs: list[dict] = []
    disputed_pubs: list[dict] = []
    verified_results: list[dict] = []
    provenance: list[str] = []
    caveats: list[str] = []

    if status == AdjudicationStatus.PASS:
        grounded, ungrounded, caveats = validate_synthesis_grounding(
            claims=active_claims,
            check_report=check_report,
            adjudication=adjudication,
        )
        for c in grounded:
            pub = _claim_public(c)
            accepted_pubs.append(pub)
            verified_results.append(pub)
            if c.computation_artifact_id:
                provenance.append(
                    f"{c.claim_id} <- computation={c.computation_artifact_id}"
                )
        for c in ungrounded:
            rejected_pubs.append(_claim_public(c))
    elif status == AdjudicationStatus.FAIL:
        for c in active_claims:
            rejected_pubs.append(_claim_public(c))
    elif status == AdjudicationStatus.DISPUTED:
        for c in active_claims:
            disputed_pubs.append(_claim_public(c))
    else:
        for c in active_claims:
            disputed_pubs.append(_claim_public(c))

    residual: list[str] = []
    open_q: list[str] = []
    if verification:
        residual.extend(verification.discrepancies)
        if verification.notes:
            open_q.append(verification.notes)
    if red_team:
        residual.append(red_team.summary)
        for atk in red_team.attacks:
            residual.append(f"[{atk.severity.value}] {atk.description}")
    if adjudication:
        open_q.extend(adjudication.reasons)
    open_q.extend(caveats)

    return SynthesisBundle(
        accepted_claims=accepted_pubs,
        rejected_claims=rejected_pubs,
        disputed_claims=disputed_pubs,
        verification_reports=[verification.model_dump(mode="json")] if verification else [],
        red_team_reports=[red_team.model_dump(mode="json")] if red_team else [],
        decisions=[d.model_dump(mode="json") for d in decisions],
        open_questions=open_q,
        residual_risks=residual,
        adjudication_status=status,
        report_gate=gate,
        narrative=narrative or "",
        verified_results=verified_results,
        caveats=caveats,
        provenance=provenance,
    )


def render_final_report(bundle: SynthesisBundle, *, llm_polish: dict | None = None) -> str:
    """
    Gate: never present DISPUTED/FAIL as proven engineering fact.

    llm_polish may supply optional prose under 'summary' but cannot change gate
    labels or invent accepted quantitative claims.
    """
    polish = llm_polish or {}
    gate = bundle.report_gate
    title_note = {
        "PASS": "ACCEPTED (adjudicated)",
        "DISPUTED": "DISPUTED — not proven",
        "FAIL": "FAILED verification — not proven",
        "INSUFFICIENT_EVIDENCE": "INCOMPLETE — insufficient evidence",
        "INCOMPLETE": "INCOMPLETE",
    }.get(gate, gate)

    lines = [
        "# Final report",
        "",
        f"**Report gate:** `{title_note}`",
        "",
    ]
    if gate != "PASS":
        lines += [
            "> This report is **not** an accepted engineering conclusion.",
            "> Disputed/failed/incomplete results must not be treated as proven facts.",
            "",
        ]

    summary = str(polish.get("summary") or bundle.narrative or "").strip()
    if summary and gate == "PASS":
        lines += ["## Narrative (non-authoritative wording)", summary, ""]
    elif summary:
        lines += ["## Narrative (non-authoritative)", summary, ""]

    if bundle.scope:
        orig = bundle.scope.get("original_problem") or ""
        obj = bundle.scope.get("objective") or ""
        status = bundle.scope.get("status") or ""
        rationale = bundle.scope.get("rationale") or ""
        lines += ["## Scope"]
        lines.append(f"- Status: `{status}`")
        if orig:
            preview = orig.strip().split("\n", 1)[0][:240]
            lines.append(f"- Original question: {preview}")
        if obj:
            lines.append(f"- Resolved objective: {obj}")
        if rationale:
            lines.append(f"- Reason: {rationale}")
        clar = bundle.scope.get("clarifications") or []
        if clar:
            lines.append("- Clarification provided by user")
        lines.append("")

    assumptions = []
    if bundle.scope:
        assumptions = list(bundle.scope.get("assumptions") or [])
    lines += ["## Assumptions"]
    if assumptions:
        for item in assumptions:
            if isinstance(item, dict):
                kind = item.get("kind") or "ASSUMPTION"
                text = item.get("text") or ""
                lines.append(f"- ({kind}) {text}")
            else:
                lines.append(f"- {item}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    lines += ["## Verified results"]
    results = bundle.verified_results or bundle.accepted_claims
    if results:
        for c in results:
            lines.append(f"- `{c['claim_id']}` ({c['kind']}): {c['statement']}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    lines += ["## Accepted claims"]
    if bundle.accepted_claims:
        for c in bundle.accepted_claims:
            lines.append(f"- `{c['claim_id']}` ({c['kind']}): {c['statement']}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    if bundle.provenance:
        lines += ["## Provenance"]
        for p in bundle.provenance:
            lines.append(f"- {p}")
        lines.append("")

    lines += ["## Disputed claims"]
    if bundle.disputed_claims:
        for c in bundle.disputed_claims:
            lines.append(f"- `{c['claim_id']}` ({c['kind']}): {c['statement']}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    lines += ["## Rejected claims"]
    if bundle.rejected_claims:
        for c in bundle.rejected_claims:
            lines.append(f"- `{c['claim_id']}` ({c['kind']}): {c['statement']}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    lines += ["## Verification"]
    if bundle.verification_reports:
        for vr in bundle.verification_reports:
            lines.append(f"- status=`{vr.get('status')}` id=`{vr.get('report_id')}`")
            for d in vr.get("discrepancies") or []:
                lines.append(f"  - discrepancy: {d}")
    else:
        lines.append("- _(missing — report incomplete)_")
    lines.append("")

    lines += ["## Red team"]
    if bundle.red_team_reports:
        for rt in bundle.red_team_reports:
            lines.append(
                f"- recommended_reject=`{rt.get('recommended_reject')}` "
                f"id=`{rt.get('report_id')}`"
            )
            lines.append(f"  - summary: {rt.get('summary')}")
            for atk in rt.get("attacks") or []:
                lines.append(
                    f"  - [{atk.get('severity')}] {atk.get('description')}"
                )
    else:
        lines.append("- _(missing — report incomplete)_")
    lines.append("")

    lines += ["## Residual risks"]
    for r in bundle.residual_risks or ["_(none)_"]:
        lines.append(f"- {r}")
    lines.append("")

    lines += ["## Open questions / caveats"]
    for q in (bundle.open_questions or []) + (bundle.caveats or []):
        lines.append(f"- {q}")
    if not bundle.open_questions and not bundle.caveats:
        lines.append("- _(none)_")
    lines.append("")

    lines += ["## Evidence gaps"]
    if bundle.evidence_gaps:
        for g in bundle.evidence_gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- _(none)_")
    if bundle.research_status:
        lines.append(f"- research_status: `{bundle.research_status}`")
    lines.append("")

    lines += [
        "## Evidence references",
        f"- adjudication_status: `{bundle.adjudication_status}`",
        f"- decisions: {len(bundle.decisions)}",
        "",
        "_Generated from SynthesisBundle. Verification/red-team/adjudication are authoritative._",
        "_LLM narrative cannot create or alter accepted quantitative results._",
        "",
    ]
    return "\n".join(lines)


def synthesis_allowed(
    bundle: SynthesisBundle,
    *,
    require_independent_review: bool = True,
    require_red_team: bool = True,
) -> bool:
    """Final report file may be written; content always reflects gate honestly.

    Non-PASS gates always allow an honest incomplete report.
    """
    if bundle.adjudication_status != AdjudicationStatus.PASS:
        return True
    if not require_independent_review:
        return True
    if not require_red_team:
        return bool(bundle.verification_reports)
    return bool(bundle.verification_reports and bundle.red_team_reports)
