"""Immutable execution binding — prevents cross-task / cross-investigation state mix.

PR-01: CalculationSpec, Claim, ComputationArtifact и ResearchResult должны
нести один и тот же ExecutionContext. Расхождение → CONTEXT_MISMATCH (fail loud).
PR-03: contract_version берётся из активного EngineeringContract (не ``unset``,
когда контракт существует).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ai_lab.core.enums import LabErrorCode


# Placeholder when no EngineeringContract is bound to the run yet.
UNSET_CONTRACT_VERSION = "unset"


class ExecutionContext(BaseModel):
    """Frozen identity of one lab execution slice (project/investigation/task/run).

    Today investigation_id == project_id (project folder is the investigation).
    When an EngineeringContract is active, contract_version must match its version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    investigation_id: str
    task_id: str
    run_id: str
    contract_version: str = UNSET_CONTRACT_VERSION

    @classmethod
    def for_project_run(
        cls,
        *,
        project_id: str,
        run_id: str,
        task_id: str,
        investigation_id: str | None = None,
        contract_version: str = UNSET_CONTRACT_VERSION,
    ) -> ExecutionContext:
        """Build context; investigation defaults to project (current on-disk model)."""
        if not project_id:
            raise ValueError("ExecutionContext.project_id is required")
        if not run_id:
            raise ValueError("ExecutionContext.run_id is required")
        if not task_id:
            raise ValueError("ExecutionContext.task_id is required")
        inv = investigation_id if investigation_id is not None else project_id
        return cls(
            project_id=project_id,
            investigation_id=inv,
            task_id=task_id,
            run_id=run_id,
            contract_version=contract_version or UNSET_CONTRACT_VERSION,
        )


class ContextMismatchError(RuntimeError):
    """Hard isolation failure — never silently remap foreign artifacts."""

    code: LabErrorCode = LabErrorCode.CONTEXT_MISMATCH

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        expected: Any = None,
        actual: Any = None,
        where: str | None = None,
    ) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        self.where = where
        parts = [f"{LabErrorCode.CONTEXT_MISMATCH.value}: {message}"]
        if where:
            parts.append(f"where={where}")
        if field:
            parts.append(f"field={field} expected={expected!r} actual={actual!r}")
        super().__init__(" | ".join(parts))


def _mismatch(
    *,
    field: str,
    expected: str,
    actual: str,
    where: str,
) -> ContextMismatchError:
    return ContextMismatchError(
        f"{field} does not match execution context",
        field=field,
        expected=expected,
        actual=actual,
        where=where,
    )


def require_context_match(
    expected: ExecutionContext,
    *,
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    contract_version: str | None = None,
    where: str = "artifact",
) -> None:
    """Fail loud if any *set* identity field disagrees with expected.

    Unset (None/empty) actual fields are allowed for legacy on-disk JSON —
    but a wrong non-empty value is never remapped.
    """
    checks: list[tuple[str, str, str | None]] = [
        ("project_id", expected.project_id, project_id),
        ("investigation_id", expected.investigation_id, investigation_id),
        ("task_id", expected.task_id, task_id),
        ("run_id", expected.run_id, run_id),
    ]
    for field, exp, act in checks:
        if act is None or act == "":
            continue
        if act != exp:
            raise _mismatch(field=field, expected=exp, actual=act, where=where)

    if contract_version is not None and contract_version != "":
        if contract_version != expected.contract_version:
            raise _mismatch(
                field="contract_version",
                expected=expected.contract_version,
                actual=contract_version,
                where=where,
            )


def require_artifact_context(
    expected: ExecutionContext,
    artifact: Any,
    *,
    where: str | None = None,
) -> None:
    """Assert a pydantic/dict-like artifact is bound to expected ExecutionContext."""
    label = where or type(artifact).__name__
    if isinstance(artifact, dict):
        require_context_match(
            expected,
            project_id=artifact.get("project_id"),
            investigation_id=artifact.get("investigation_id"),
            task_id=artifact.get("task_id"),
            run_id=artifact.get("run_id"),
            contract_version=artifact.get("contract_version"),
            where=label,
        )
        return
    require_context_match(
        expected,
        project_id=getattr(artifact, "project_id", None),
        investigation_id=getattr(artifact, "investigation_id", None),
        task_id=getattr(artifact, "task_id", None),
        run_id=getattr(artifact, "run_id", None),
        contract_version=getattr(artifact, "contract_version", None),
        where=label,
    )


def stamp_context_fields(
    expected: ExecutionContext,
    *,
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    contract_version: str | None = None,
    where: str = "artifact",
) -> dict[str, str]:
    """Return identity fields to write: verify mismatches, fill only unset slots.

    Never overwrites a wrong value — that would be silent remapping.
    """
    require_context_match(
        expected,
        project_id=project_id,
        investigation_id=investigation_id,
        task_id=task_id,
        run_id=run_id,
        contract_version=contract_version,
        where=where,
    )
    return {
        "project_id": project_id or expected.project_id,
        "investigation_id": investigation_id or expected.investigation_id,
        "task_id": task_id or expected.task_id,
        "run_id": run_id or expected.run_id,
        "contract_version": contract_version or expected.contract_version,
    }


def context_binding_dict(ctx: ExecutionContext) -> dict[str, str]:
    """Flat dict for model_copy / constructors."""
    return {
        "project_id": ctx.project_id,
        "investigation_id": ctx.investigation_id,
        "task_id": ctx.task_id,
        "run_id": ctx.run_id,
        "contract_version": ctx.contract_version,
    }


def attach_research_result_to_context(
    result: Any,
    expected: ExecutionContext,
    *,
    where: str = "ResearchResult",
) -> Any:
    """Bind ResearchResult to the calling task; refuse foreign task/investigation.

    Returns a model_copy with stamped identity fields. Wrong ids → CONTEXT_MISMATCH.
    """
    require_artifact_context(expected, result, where=where)
    stamped = stamp_context_fields(
        expected,
        project_id=getattr(result, "project_id", None),
        investigation_id=getattr(result, "investigation_id", None),
        task_id=getattr(result, "task_id", None),
        run_id=getattr(result, "run_id", None),
        contract_version=getattr(result, "contract_version", None),
        where=where,
    )
    if hasattr(result, "model_copy"):
        return result.model_copy(update=stamped)
    for key, value in stamped.items():
        setattr(result, key, value)
    return result


def resume_execution_context(
    *,
    project_id: str,
    run_id: str,
    task_id: str,
    investigation_id: str | None = None,
    contract_version: str = UNSET_CONTRACT_VERSION,
    previous: ExecutionContext | None = None,
) -> ExecutionContext:
    """Rebuild context on --resume; project/run/investigation must match prior run."""
    ctx = ExecutionContext.for_project_run(
        project_id=project_id,
        investigation_id=investigation_id,
        task_id=task_id,
        run_id=run_id,
        contract_version=contract_version,
    )
    if previous is not None:
        # Resume may advance task_id (next graph node) but never project/run/investigation.
        require_context_match(
            previous,
            project_id=ctx.project_id,
            investigation_id=ctx.investigation_id,
            run_id=ctx.run_id,
            where="resume_execution_context",
        )
        if previous.contract_version != ctx.contract_version:
            raise ContextMismatchError(
                "contract_version changed across resume",
                field="contract_version",
                expected=previous.contract_version,
                actual=ctx.contract_version,
                where="resume_execution_context",
            )
    return ctx
