"""Deterministic synthesis bundle + gated final report rendering."""

from __future__ import annotations

from ai_lab.core.enums import AdjudicationStatus, AttackSeverity, VerificationStatus
from ai_lab.core.models import (
    AdjudicationResult,
    Claim,
    DecisionRecord,
    RedTeamReport,
    SynthesisBundle,
    VerificationReport,
)


def build_synthesis_bundle(
    *,
    claims: list[Claim],
    verification: VerificationReport | None,
    red_team: RedTeamReport | None,
    decisions: list[DecisionRecord],
    adjudication: AdjudicationResult | None,
) -> SynthesisBundle:
    """Classify claims from review state — LLM does not decide what is proven."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    disputed: list[dict] = []

    status = adjudication.status if adjudication else AdjudicationStatus.INSUFFICIENT_EVIDENCE
    gate = status.value

    active_claims = [c for c in claims if not c.superseded_by]

    if status == AdjudicationStatus.PASS:
        for c in active_claims:
            accepted.append(_claim_public(c))
    elif status == AdjudicationStatus.FAIL:
        for c in active_claims:
            rejected.append(_claim_public(c))
    elif status == AdjudicationStatus.DISPUTED:
        for c in active_claims:
            disputed.append(_claim_public(c))
    else:
        for c in active_claims:
            disputed.append(_claim_public(c))

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

    return SynthesisBundle(
        accepted_claims=accepted,
        rejected_claims=rejected,
        disputed_claims=disputed,
        verification_reports=[verification.model_dump(mode="json")] if verification else [],
        red_team_reports=[red_team.model_dump(mode="json")] if red_team else [],
        decisions=[d.model_dump(mode="json") for d in decisions],
        open_questions=open_q,
        residual_risks=residual,
        adjudication_status=status,
        report_gate=gate,
    )


def _claim_public(claim: Claim) -> dict:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "kind": claim.kind.value,
        "source": claim.source,
        "source_trust": claim.source_trust.value if claim.source_trust else None,
        "version": claim.version,
        "refs": claim.refs,
    }


def render_final_report(bundle: SynthesisBundle, *, llm_polish: dict | None = None) -> str:
    """
    Gate: never present DISPUTED/FAIL as proven engineering fact.

    llm_polish may supply optional prose under 'summary' but cannot change gate labels.
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

    summary = str(polish.get("summary") or "").strip()
    if summary and gate == "PASS":
        lines += ["## Summary", summary, ""]
    elif summary:
        lines += ["## Narrative (non-authoritative)", summary, ""]

    lines += ["## Accepted claims"]
    if bundle.accepted_claims:
        for c in bundle.accepted_claims:
            lines.append(f"- `{c['claim_id']}` ({c['kind']}): {c['statement']}")
    else:
        lines.append("- _(none)_")
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

    lines += ["## Open questions"]
    for q in bundle.open_questions or ["_(none)_"]:
        lines.append(f"- {q}")
    lines.append("")

    lines += [
        "## Evidence references",
        f"- adjudication_status: `{bundle.adjudication_status}`",
        f"- decisions: {len(bundle.decisions)}",
        "",
        "_Generated from SynthesisBundle. Verification/red-team/adjudication are authoritative._",
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

    SIMPLE profile: adjudication + deterministic path — V/RT reports not required.
    STANDARD: verification required; red team optional.
    COMPLEX/RESEARCH: both required (defaults).
    """
    if not require_independent_review:
        return bundle.adjudication_status == AdjudicationStatus.PASS
    if not require_red_team:
        return bool(bundle.verification_reports)
    return bool(bundle.verification_reports and bundle.red_team_reports)
