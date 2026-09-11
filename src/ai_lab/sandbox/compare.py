"""Compare two ComputationArtifacts for reproducibility identity — not scientific PASS/FAIL."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_lab.core.enums import ReproductionVerdict
from ai_lab.core.models import ComputationArtifact


class ReproductionReport(BaseModel):
    verdict: ReproductionVerdict
    identity_match: bool
    output_match: bool
    differing_identity_fields: list[str] = Field(default_factory=list)
    differing_output_fields: list[str] = Field(default_factory=list)


_IDENTITY_FIELDS = (
    "code_hash",
    "input_hash",
    "environment_hash",
    "python_version",
    "dependency_hash",
    "sandbox_policy_version",
    "sandbox_backend",
    "computation_hash",
    "image_digest",
)

_OUTPUT_FIELDS = ("stdout_hash", "stderr_hash", "returncode", "sandbox_status")


def compare_computations(a: ComputationArtifact, b: ComputationArtifact) -> ReproductionReport:
    """Why two runs are or are not the same computation. No tolerance / science here."""
    identity_diff = [
        name for name in _IDENTITY_FIELDS if getattr(a, name, None) != getattr(b, name, None)
    ]
    output_diff = [
        name for name in _OUTPUT_FIELDS if getattr(a, name, None) != getattr(b, name, None)
    ]
    identity_match = not identity_diff
    output_match = not output_diff
    if not identity_match:
        verdict = ReproductionVerdict.IDENTITY_DIFFERENT
    elif not output_match:
        verdict = ReproductionVerdict.REPRODUCTION_MISMATCH
    elif _environment_is_partial(a) or _environment_is_partial(b):
        # Без image digest это не strong environment equivalence.
        verdict = ReproductionVerdict.PARTIAL_ENVIRONMENT
    else:
        verdict = ReproductionVerdict.REPRODUCTION_MATCH
    return ReproductionReport(
        verdict=verdict,
        identity_match=identity_match,
        output_match=output_match,
        differing_identity_fields=identity_diff,
        differing_output_fields=output_diff,
    )


def _environment_is_partial(art: ComputationArtifact) -> bool:
    if (art.environment_reproducibility or "").lower() == "partial":
        return True
    if art.sandbox_backend == "docker" and not art.image_digest:
        return True
    return False
