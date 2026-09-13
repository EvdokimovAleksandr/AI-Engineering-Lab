"""PR-05: deterministic Claim/Evidence lineage + contract coverage verifiers.

Not LLM agents. Failures surface as INSUFFICIENT_EVIDENCE via evidence completeness
(or CONTEXT_MISMATCH when identity fields disagree). Unit/NumericalVerifier remain
in calculation_contract / DeterministicVerifier — this module only covers lineage.
"""

from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.enums import ClaimSupportStatus, EvidenceType, LabErrorCode
from ai_lab.core.execution_context import (
    ContextMismatchError,
    ExecutionContext,
    require_context_match,
)
from ai_lab.core.models import Claim, ComputationArtifact
from ai_lab.knowledge.models import EvidenceRecord
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Статусы, которые synthesis может считать «доказанными» (при наличии lineage).
_ACCEPTED_SUPPORT: frozenset[ClaimSupportStatus] = frozenset(
    {ClaimSupportStatus.SUPPORTED, ClaimSupportStatus.WEAKLY_SUPPORTED}
)

# PROPOSED / UNVERIFIED не считаются количественной истиной.
_UNPROVEN_SUPPORT: frozenset[ClaimSupportStatus] = frozenset(
    {
        ClaimSupportStatus.PROPOSED,
        ClaimSupportStatus.UNVERIFIED,
        ClaimSupportStatus.CONTRADICTED,
        ClaimSupportStatus.REJECTED,
    }
)


class LineageCoverageReport(BaseModel):
    """Результат LineageVerifier + ContractVerifier (без LLM)."""

    model_config = ConfigDict(extra="forbid")

    lineage_ok: bool = True
    contract_coverage_ok: bool = True
    covered_outputs: list[str] = Field(default_factory=list)
    missing_outputs: list[str] = Field(default_factory=list)
    # covered / required; None если required_outputs пуст (нет контрактного покрытия).
    coverage_ratio: float | None = None
    reasons: list[str] = Field(default_factory=list)


def require_evidence_type_refs(
    evidence: EvidenceRecord,
    *,
    where: str = "EvidenceRecord",
) -> None:
    """CALCULATED → computation ref; LITERATURE → source_id. Fail loud, no fallback."""
    if evidence.evidence_type == EvidenceType.CALCULATED:
        if not (evidence.computation_artifact_id or "").strip():
            raise ValueError(
                f"{where}: CALCULATED evidence {evidence.evidence_id!r} "
                "requires computation_artifact_id"
            )
    if evidence.evidence_type == EvidenceType.SIMULATED:
        if not (evidence.computation_artifact_id or "").strip():
            raise ValueError(
                f"{where}: SIMULATED evidence {evidence.evidence_id!r} "
                "requires computation_artifact_id"
            )
    if evidence.evidence_type == EvidenceType.LITERATURE:
        if not (evidence.source_id or "").strip():
            raise ValueError(
                f"{where}: LITERATURE evidence {evidence.evidence_id!r} requires source_id"
            )


def set_claim_support_status(
    claim: Claim,
    status: ClaimSupportStatus,
) -> Claim:
    """Перевести support_status; SUPPORTED без lineage падает (не silent)."""
    if status == ClaimSupportStatus.SUPPORTED and not claim.has_supporting_lineage:
        raise ValueError(
            f"claim {claim.claim_id} without evidence cannot become SUPPORTED"
        )
    return claim.model_copy(update={"support_status": status})


def attach_evidence_to_claim(
    claim: Claim,
    evidence: EvidenceRecord,
    *,
    expected: ExecutionContext | None = None,
) -> Claim:
    """Привязать evidence_id к claim с проверкой investigation/run lineage.

    Чужое investigation → CONTEXT_MISMATCH (не remapping).
    """
    require_evidence_type_refs(evidence, where="attach_evidence_to_claim")

    # Claim и evidence должны принадлежать одному расследованию.
    if evidence.investigation_id and claim.investigation_id:
        if evidence.investigation_id != claim.investigation_id:
            raise ContextMismatchError(
                "Evidence.investigation_id does not match Claim.investigation_id",
                field="investigation_id",
                expected=claim.investigation_id,
                actual=evidence.investigation_id,
                where="attach_evidence_to_claim",
            )
    if evidence.run_id and claim.run_id:
        if evidence.run_id != claim.run_id:
            raise ContextMismatchError(
                "Evidence.run_id does not match Claim.run_id",
                field="run_id",
                expected=claim.run_id,
                actual=evidence.run_id,
                where="attach_evidence_to_claim",
            )
    if evidence.project_id and claim.project_id:
        if evidence.project_id != claim.project_id:
            raise ContextMismatchError(
                "Evidence.project_id does not match Claim.project_id",
                field="project_id",
                expected=claim.project_id,
                actual=evidence.project_id,
                where="attach_evidence_to_claim",
            )

    if expected is not None:
        require_context_match(
            expected,
            project_id=claim.project_id,
            investigation_id=claim.investigation_id,
            task_id=claim.task_id,
            run_id=claim.run_id,
            contract_version=claim.contract_version,
            where="attach_evidence_to_claim.claim",
        )
        require_context_match(
            expected,
            project_id=evidence.project_id,
            investigation_id=evidence.investigation_id,
            task_id=evidence.task_id,
            run_id=evidence.run_id,
            contract_version=evidence.contract_version,
            where="attach_evidence_to_claim.evidence",
        )

    ev_ids = list(claim.evidence_ids or [])
    if evidence.evidence_id not in ev_ids:
        ev_ids.append(evidence.evidence_id)
    calc_ids = list(claim.calculation_ids or [])
    if evidence.computation_artifact_id and evidence.computation_artifact_id not in calc_ids:
        calc_ids.append(evidence.computation_artifact_id)
    return claim.model_copy(update={"evidence_ids": ev_ids, "calculation_ids": calc_ids})


def verify_claim_computation_lineage(
    claim: Claim,
    computation: ComputationArtifact,
    *,
    where: str = "LineageVerifier",
) -> None:
    """Claim и ComputationArtifact должны разделять investigation/run (и contract при наличии)."""
    pairs = (
        ("investigation_id", claim.investigation_id, computation.investigation_id),
        ("run_id", claim.run_id, computation.run_id),
        ("project_id", claim.project_id, computation.project_id),
    )
    for field, left, right in pairs:
        if left and right and left != right:
            raise ContextMismatchError(
                f"Claim/{field} does not match ComputationArtifact",
                field=field,
                expected=left,
                actual=right,
                where=where,
            )
    if claim.contract_version and computation.contract_version:
        if claim.contract_version != computation.contract_version:
            raise ContextMismatchError(
                "Claim.contract_version does not match ComputationArtifact",
                field="contract_version",
                expected=claim.contract_version,
                actual=computation.contract_version,
                where=where,
            )
    # task_id: только если оба заданы и явно расходятся на bound calculation
    # (orphan cross-task artifacts). Не требуем совпадения с adjudication task.


def claim_is_synthesis_proven(claim: Claim) -> bool:
    """Может ли claim войти в accepted quantitative synthesis (PR-05 gate)."""
    if claim.support_status in _UNPROVEN_SUPPORT:
        return False
    if claim.support_status in _ACCEPTED_SUPPORT and not claim.has_supporting_lineage:
        return False
    return claim.support_status in _ACCEPTED_SUPPORT


def _claim_covers_name(claim: Claim, names: Sequence[str]) -> bool:
    covers = None
    if isinstance(claim.conditions, dict):
        covers = claim.conditions.get("covers_outputs")
    if isinstance(covers, (list, tuple, set)):
        cover_set = {str(x).lower() for x in covers}
        if any(n.lower() in cover_set for n in names):
            return True
    text = (claim.statement or "").lower()
    return any(n.lower() in text for n in names)


def evaluate_lineage_and_contract_coverage(
    *,
    claims: Sequence[Claim],
    computations: Sequence[ComputationArtifact] | None = None,
    evidence: Sequence[EvidenceRecord] | None = None,
    required_outputs: Sequence[str] | None = None,
    expected: ExecutionContext | None = None,
    output_aliases: dict[str, list[str]] | None = None,
) -> LineageCoverageReport:
    """ContractVerifier + LineageVerifier в одном детерминированном проходе.

    - Lineage: claim/evidence/computation identity согласованы (иначе reason + lineage_ok=False;
      жёсткий mismatch при attach уже бросает CONTEXT_MISMATCH).
    - Contract coverage: каждый required output имеет claim с lineage
      (evidence_ids или calculation/computation) и не PROPOSED/UNVERIFIED как единственная опора.
    """
    reasons: list[str] = []
    lineage_ok = True
    aliases = output_aliases or {}
    comps = list(computations or [])
    comps_by_id = {c.artifact_id: c for c in comps}
    evidence_list = list(evidence or [])
    evidence_by_id = {e.evidence_id: e for e in evidence_list}

    for ev in evidence_list:
        try:
            require_evidence_type_refs(ev, where="LineageVerifier.evidence")
        except ValueError as exc:
            lineage_ok = False
            reasons.append(str(exc))
            logger.error("Evidence type ref check failed: %s", exc)

    for claim in claims:
        if expected is not None:
            try:
                # task_id намеренно не сверяем с expected: claims из разных узлов графа
                # (calculation vs research) живут в одном run — это не CONTEXT_MISMATCH.
                require_context_match(
                    expected,
                    project_id=claim.project_id,
                    investigation_id=claim.investigation_id,
                    task_id=None,
                    run_id=claim.run_id,
                    contract_version=claim.contract_version,
                    where=f"LineageVerifier.claim:{claim.claim_id}",
                )
            except ContextMismatchError as exc:
                lineage_ok = False
                reasons.append(str(exc))
                logger.error("Claim lineage mismatch: %s", exc)
                continue

        for calc_id in claim.calculation_ids or []:
            art = comps_by_id.get(calc_id)
            if art is None:
                # Отсутствующий артефакт — coverage/provenance, не обязательно CONTEXT.
                continue
            try:
                verify_claim_computation_lineage(
                    claim, art, where=f"LineageVerifier.calc:{claim.claim_id}"
                )
            except ContextMismatchError as exc:
                lineage_ok = False
                reasons.append(str(exc))
                logger.error("Claim↔computation lineage mismatch: %s", exc)

        if claim.computation_artifact_id:
            art = comps_by_id.get(claim.computation_artifact_id)
            if art is not None:
                try:
                    verify_claim_computation_lineage(
                        claim, art, where=f"LineageVerifier.comp:{claim.claim_id}"
                    )
                except ContextMismatchError as exc:
                    lineage_ok = False
                    reasons.append(str(exc))

        for eid in claim.evidence_ids or []:
            ev = evidence_by_id.get(eid)
            if ev is None:
                continue
            if ev.investigation_id and claim.investigation_id:
                if ev.investigation_id != claim.investigation_id:
                    lineage_ok = False
                    msg = (
                        f"{LabErrorCode.CONTEXT_MISMATCH.value}: evidence {eid} "
                        f"investigation_id={ev.investigation_id!r} vs claim "
                        f"{claim.investigation_id!r}"
                    )
                    reasons.append(msg)
                    logger.error("%s", msg)

    targets = list(required_outputs or [])
    covered: list[str] = []
    missing: list[str] = []
    if not targets:
        return LineageCoverageReport(
            lineage_ok=lineage_ok,
            contract_coverage_ok=True,
            covered_outputs=[],
            missing_outputs=[],
            coverage_ratio=None,
            reasons=reasons,
        )

    for req in targets:
        names = [req, *aliases.get(req, [])]
        ok = False
        for claim in claims:
            if claim.superseded_by:
                continue
            if not _claim_covers_name(claim, names):
                continue
            # Контракт требует claim+evidence lineage; REJECTED/CONTRADICTED не закрывают.
            # PROPOSED с lineage допустим для coverage (ещё не elevated) —
            # synthesis отдельно запрещает PROPOSED/UNVERIFIED как proven truth.
            if claim.support_status in {
                ClaimSupportStatus.REJECTED,
                ClaimSupportStatus.CONTRADICTED,
            }:
                continue
            if not claim.has_supporting_lineage:
                continue
            ok = True
            break
        if ok:
            covered.append(req)
        else:
            missing.append(req)
            reasons.append(
                f"required contract output {req!r} lacks claim+evidence coverage"
            )

    contract_ok = not missing
    ratio = (len(covered) / len(targets)) if targets else None
    return LineageCoverageReport(
        lineage_ok=lineage_ok,
        contract_coverage_ok=contract_ok,
        covered_outputs=covered,
        missing_outputs=missing,
        coverage_ratio=ratio,
        reasons=reasons,
    )


def merge_lineage_into_completeness_fields(
    report: LineageCoverageReport,
) -> dict[str, Any]:
    """Поля для EvidenceCompletenessReport / adjudication metadata (PR-06 hook)."""
    return {
        "lineage_ok": report.lineage_ok,
        "contract_coverage_ok": report.contract_coverage_ok,
        "coverage_ratio": report.coverage_ratio,
        "lineage_reasons": list(report.reasons),
        "covered_outputs": list(report.covered_outputs),
        "missing_outputs": list(report.missing_outputs),
    }
