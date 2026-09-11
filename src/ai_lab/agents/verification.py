"""Verification Agent — interprets DeterministicCheckReport; cannot override CheckStatus."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.checks.verifier import NON_OVERRIDABLE_FAIL_STATUSES
from ai_lab.core.enums import AgreementType, AgentRole, CheckStatus, VerificationStatus
from ai_lab.core.models import AgentResult, ReviewBundle, TaskSpec, VerificationReport


class VerificationAgent(BaseAgent):
    role = AgentRole.VERIFICATION
    system_prompt = (
        "You are the Verification Agent. Do NOT trust other agents. "
        "You receive a blind ReviewBundle and DeterministicCheckReport. "
        "If deterministic checks failed, status MUST be FAIL. "
        "You may interpret but cannot override deterministic CheckStatus "
        "(PASS/FAIL/INCOMPATIBLE_DIMENSIONS/OUT_OF_BOUNDS/...). JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        bundle = await self._load_bundle(task, ctx)
        check = bundle.check_report
        det_statuses = _deterministic_statuses(check)
        det_fail = bool(check and check.has_critical_failure)
        det_pass = bool(check and check.results and check.all_passed)
        discrepancies = list(check.critical_failures) if check else []
        # Surface dimensional failures as their own diagnostic, not a generic FAIL.
        for status in det_statuses:
            if status == CheckStatus.INCOMPATIBLE_DIMENSIONS:
                discrepancies.append("INCOMPATIBLE_DIMENSIONS")

        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                "Blind ReviewBundle (no author confidence / no peer reviews):\n"
                f"{bundle.model_dump(mode='json')}\n"
                "Return JSON: {status, discrepancies, recomputed, notes}. "
                "Do not change deterministic CheckStatus values."
            ),
            schema_name="VerificationReport",
            extra_metadata={
                "deterministic_critical_failure": det_fail,
                "deterministic_all_passed": det_pass,
                "deterministic_statuses": [s.value for s in det_statuses],
                "check_discrepancies": discrepancies,
            },
        )

        status_raw = str(payload.get("status") or VerificationStatus.INSUFFICIENT_EVIDENCE.value)
        try:
            status = VerificationStatus(status_raw)
        except ValueError:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE

        # Hard gate: LLM cannot promote a deterministic non-PASS into PASS
        # and cannot rewrite the numeric CheckStatus payload.
        if det_fail or any(s in NON_OVERRIDABLE_FAIL_STATUSES for s in det_statuses):
            status = VerificationStatus.FAIL
            discrepancies = list(dict.fromkeys(discrepancies + list(payload.get("discrepancies") or [])))

        report = VerificationReport(
            target_claim_ids=list(bundle.target_claim_ids),
            status=status,
            discrepancies=discrepancies or list(payload.get("discrepancies") or []),
            recomputed=_locked_recomputed(check, payload.get("recomputed")),
            notes=str(payload.get("notes") or ""),
            agent_id=self.role.value,
            check_report_ids=[check.report_id] if check else [],
            agreement_type=(
                AgreementType.INDEPENDENT_EVIDENCE
                if det_pass or det_fail
                else AgreementType.CONSENSUS
            ),
        )
        path = f"reviews/{report.report_id}.json"
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path=path,
            data=report.model_dump(mode="json"),
        )
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="reviews/last_verification.json",
            data=report.model_dump(mode="json"),
        )
        if ctx.run_store is not None:
            ctx.run_store.save_review_json(
                f"{report.report_id}.json", report.model_dump(mode="json")
            )

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Verification status={report.status.value}",
            verification=report,
            artifact_paths=[path, "reviews/last_verification.json"],
            raw=payload,
        )

    async def _load_bundle(self, task: TaskSpec, ctx: AgentContext) -> ReviewBundle:
        if task.review_bundle_path:
            data = ctx.store.read_json(task.review_bundle_path)
            return ReviewBundle.model_validate(data)
        # Fallback for unit tests: empty bundle
        return ReviewBundle(run_id=ctx.run_id, claims=[])


def _deterministic_statuses(check) -> list[CheckStatus]:
    if check is None:
        return []
    statuses: list[CheckStatus] = []
    for vr in getattr(check, "verification_results", None) or []:
        statuses.append(vr.status)
    for row in getattr(check, "results", None) or []:
        if row.status is not None:
            statuses.append(row.status)
    return statuses


def _locked_recomputed(check, llm_recomputed) -> dict:
    """LLM may explain; it must not replace deterministic numbers/status."""
    deterministic: dict = {}
    if check is not None:
        deterministic = {
            "verification_results": [
                r.model_dump(mode="json") for r in (check.verification_results or [])
            ],
            "math_results": [r.model_dump(mode="json") for r in (check.results or [])],
        }
    return {
        "deterministic": deterministic,
        "llm_interpretation": llm_recomputed or {},
    }
