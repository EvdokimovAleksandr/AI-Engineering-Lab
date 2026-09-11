"""Deterministic MockProvider for offline workflow demos and tests.

PASS is never based on verification call count.
PASS requires fixture conditions (e.g. deterministic checks passed).
"""

from __future__ import annotations

import json
from typing import Any

from ai_lab.core.enums import AgentRole
from ai_lab.core.models import LLMRequest, LLMResponse
from ai_lab.llm.base import extract_json_object
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


class MockProvider:
    """Returns structured JSON keyed by agent role in request metadata."""

    name = "mock"

    def __init__(
        self,
        *,
        force_verification_fail: bool = False,
        # If set, verification always returns this status (tests)
        verification_status_override: str | None = None,
    ) -> None:
        self.force_verification_fail = force_verification_fail
        self.verification_status_override = verification_status_override
        self._verification_calls = 0  # metrics only — NEVER drives PASS

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.response_schema_name == "TaskGraphProposal":
            payload = self._planner_proposal()
            content = json.dumps(payload, ensure_ascii=False)
            parsed = extract_json_object(content)
            model = request.model or "mock-deterministic"
            return LLMResponse(
                content=content,
                parsed=parsed,
                model=model,
                provider=self.name,
                run_id="mock_planner",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            )
        role_raw = request.metadata.get("agent_role", AgentRole.CHIEF_ENGINEER.value)
        try:
            role = AgentRole(role_raw)
        except ValueError:
            role = AgentRole.CHIEF_ENGINEER
            logger.error("Unknown agent_role in LLM metadata: %s", role_raw)

        payload = self._payload_for(role, request)
        content = json.dumps(payload, ensure_ascii=False)
        parsed = extract_json_object(content)
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        # Echo the routed model id so independence tests can distinguish A/B/C.
        model = request.model or "mock-deterministic"
        return LLMResponse(
            content=content,
            parsed=parsed,
            model=model,
            provider=self.name,
            run_id=f"mock_{role.value}",
            usage=usage,
        )

    def _payload_for(self, role: AgentRole, request: LLMRequest) -> dict[str, Any]:
        if role == AgentRole.CHIEF_ENGINEER:
            return {
                "summary": "Decomposed problem into research, analysis, and verification tracks.",
                "understanding": "Industrial spider silk requires host expression and fiber spinning.",
                "unknowns": [
                    "scalable spinning yield",
                    "true industrial tensile strength under plant conditions",
                ],
                "follow_up_roles": [
                    "research",
                    "theorist",
                    "simulation",
                    "verification",
                    "red_team",
                ],
            }
        if role == AgentRole.RESEARCH:
            return {
                "findings": [
                    {
                        "statement": (
                            "Major ampullate spider silk tensile strength is often reported "
                            "near 1–2 GPa in lab literature."
                        ),
                        "kind": "INFERENCE",
                        "source": "mock://literature-survey",
                        "source_trust": "STUB",
                        "evidence": "Aggregated secondary reports in mock research stub",
                        "conditions": {"environment": "lab", "humidity": "controlled"},
                        "relevance": 0.8,
                        "potential_contradiction": "Industrial fibers may not reach lab values",
                        "falsifiers": ["Independent tensile test on industrial fiber < 0.5 GPa"],
                        "assumptions": ["Literature values refer to dragline silk"],
                    }
                ]
            }
        if role == AgentRole.THEORIST:
            return {
                "claims": [
                    {
                        "statement": (
                            "If fiber diameter doubles at constant mass flow, drawing stress "
                            "drops roughly with cross-section growth."
                        ),
                        "kind": "INFERENCE",
                        "evidence": "Dimensional scaling of stress = force/area",
                        "assumptions": ["Incompressible fiber approximation"],
                        "falsifiers": ["Measured stress independent of diameter under same force"],
                    }
                ],
                "hypotheses": [
                    {
                        "statement": (
                            "Spinning rate is limited by chain alignment kinetics more than "
                            "by expression titer."
                        ),
                        "prediction": (
                            "Increasing titer without slower draw will not raise toughness "
                            "proportionally"
                        ),
                        "falsification_criteria": [
                            "Equal toughness gain when titer rises at fixed draw rate"
                        ],
                        "assumptions": ["Alignment is the dominant toughening mechanism"],
                    }
                ],
            }
        if role == AgentRole.SIMULATION:
            # Correct stress formula with embedded math_check for independent recompute
            diameter_m = 5.0e-6
            force_n = 0.05
            import math as _math

            area = _math.pi * (diameter_m / 2) ** 2
            expected = (force_n / area) / 1e9
            return {
                "code": (
                    "import math\n"
                    "diameter_m = 5.0e-6\n"
                    "force_n = 0.05\n"
                    "area_m2 = math.pi * (diameter_m / 2) ** 2\n"
                    "stress_gpa = (force_n / area_m2) / 1e9\n"
                    "print(round(stress_gpa, 6))\n"
                ),
                "claims": [
                    {
                        "statement": (
                            "Example stress for 0.05 N on 5 µm fiber is on the order of "
                            "GPa-scale under idealized geometry."
                        ),
                        "kind": "CALCULATION",
                        "assumptions": ["Circular cross-section", "Uniform load"],
                        "falsifiers": ["Non-circular industrial fiber geometry"],
                        "math_check": {
                            "expression": "(force_n / (pi * (diameter_m / 2) ** 2)) / 1e9",
                            "expected": round(expected, 6),
                            "tolerance": 1e-4,
                            "inputs": {"force_n": force_n, "diameter_m": diameter_m},
                            "units": {"force_n": "N", "diameter_m": "m"},
                            "required_units": {"force_n": "N", "diameter_m": "m"},
                        },
                    }
                ],
            }
        if role == AgentRole.VERIFICATION:
            self._verification_calls += 1
            # Fixture-driven status — NEVER "call_count >= 2 → PASS"
            meta = request.metadata or {}
            if self.force_verification_fail:
                status = "FAIL"
            elif self.verification_status_override:
                status = self.verification_status_override
            elif meta.get("deterministic_critical_failure"):
                status = "FAIL"
            elif meta.get("deterministic_all_passed"):
                status = "PASS"
            elif meta.get("verification_fixture_status"):
                status = str(meta["verification_fixture_status"])
            else:
                # No check signal → insufficient, not a fake PASS
                status = "INSUFFICIENT_EVIDENCE"
            return {
                "status": status,
                "discrepancies": list(meta.get("check_discrepancies") or [])
                or [
                    "Lab GPa figures lack industrial humidity/strain-rate conditions",
                    "Mock research source is not a primary measurement",
                ],
                "recomputed": {"note": "Interpreted DeterministicCheckReport; see check results"},
                "notes": (
                    "Deterministic checks reported critical failure."
                    if status == "FAIL"
                    else "Checks passed; claims remain provisional for industrial conditions."
                    if status == "PASS"
                    else "Insufficient independent compute evidence for PASS."
                ),
            }
        if role == AgentRole.RED_TEAM:
            return {
                "recommended_reject": False,
                "summary": "Scaling claims remain weakly supported; treat industrial GPa as unproven.",
                "attacks": [
                    {
                        "description": (
                            "Extrapolating lab tensile strength to plant-scale fiber without "
                            "process equivalence."
                        ),
                        "severity": "MEDIUM",
                        "category": "erroneous_extrapolation",
                        "target_claim_ids": [],
                    },
                    {
                        "description": "Hidden dependency on controlled humidity not stated as assumption.",
                        "severity": "MEDIUM",
                        "category": "bad_assumption",
                        "target_claim_ids": [],
                    },
                ],
            }
        return {
            "summary": f"Mock noop for role {role.value}",
            "notes": request.messages[-1].content[:200],
        }

    def _planner_proposal(self) -> dict[str, Any]:
        """Structured TaskGraphProposal only — never executes tools or agents."""
        from ai_lab.planner.static import StaticPlanner, tasks_to_proposal_dicts, default_pipeline_tasks

        return {
            "graph_id": "static_pipeline",
            "version": 1,
            "tasks": tasks_to_proposal_dicts(default_pipeline_tasks()),
            "metadata": {"planner": StaticPlanner.name, "source": "mock"},
        }
