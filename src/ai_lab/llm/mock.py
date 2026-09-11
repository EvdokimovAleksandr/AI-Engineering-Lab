"""Deterministic MockProvider for offline workflow demos and tests."""

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

    def __init__(self, *, force_verification_fail: bool = False) -> None:
        # When True, verification always reports FAIL (for transition unit tests).
        self.force_verification_fail = force_verification_fail
        # First verification is DISPUTED to force ITERATION_REQUIRED; later PASS.
        self._verification_calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        role_raw = request.metadata.get("agent_role", AgentRole.CHIEF_ENGINEER.value)
        try:
            role = AgentRole(role_raw)
        except ValueError:
            role = AgentRole.CHIEF_ENGINEER
            logger.error("Unknown agent_role in LLM metadata: %s", role_raw)

        payload = self._payload_for(role, request)
        content = json.dumps(payload, ensure_ascii=False)
        # Validate our own JSON is parseable via shared helper
        parsed = extract_json_object(content)
        return LLMResponse(
            content=content,
            parsed=parsed,
            model="mock-deterministic",
            provider=self.name,
            run_id=f"mock_{role.value}",
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
                        "statement": "Major ampullate spider silk tensile strength is often reported near 1–2 GPa in lab literature.",
                        "kind": "INFERENCE",
                        "source": "mock://literature-survey",
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
                        "statement": "If fiber diameter doubles at constant mass flow, drawing stress drops roughly with cross-section growth.",
                        "kind": "INFERENCE",
                        "evidence": "Dimensional scaling of stress = force/area",
                        "assumptions": ["Incompressible fiber approximation"],
                        "falsifiers": ["Measured stress independent of diameter under same force"],
                    }
                ],
                "hypotheses": [
                    {
                        "statement": "Spinning rate is limited by chain alignment kinetics more than by expression titer.",
                        "prediction": "Increasing titer without slower draw will not raise toughness proportionally",
                        "falsification_criteria": [
                            "Equal toughness gain when titer rises at fixed draw rate"
                        ],
                        "assumptions": ["Alignment is the dominant toughening mechanism"],
                    }
                ],
            }
        if role == AgentRole.SIMULATION:
            return {
                "code": (
                    "import math\n"
                    "diameter_um = 5.0\n"
                    "force_un = 0.05\n"
                    "area_m2 = math.pi * (diameter_um * 1e-6 / 2) ** 2\n"
                    "stress_gpa = (force_un / area_m2) / 1e9\n"
                    "print(round(stress_gpa, 4))\n"
                ),
                "claims": [
                    {
                        "statement": "Example stress for 0.05 N on 5 µm fiber is on the order of GPa-scale under idealized geometry.",
                        "kind": "CALCULATION",
                        "assumptions": ["Circular cross-section", "Uniform load"],
                        "falsifiers": ["Non-circular industrial fiber geometry"],
                    }
                ],
            }
        if role == AgentRole.VERIFICATION:
            self._verification_calls += 1
            if self.force_verification_fail:
                status = "FAIL"
            elif self._verification_calls == 1:
                # First pass disputes → orchestrator must iterate (non-linear workflow).
                status = "DISPUTED"
            else:
                status = "PASS"
            return {
                "status": status,
                "discrepancies": [
                    "Lab GPa figures lack industrial humidity/strain-rate conditions",
                    "Mock research source is not a primary measurement",
                ],
                "recomputed": {"note": "Independent dimensional check only; no primary data"},
                "notes": (
                    "Independent verification rejects treating literature GPa as FACT "
                    "for industrial fiber."
                    if status != "PASS"
                    else "After iteration, claims remain provisional but internally consistent."
                ),
            }
        if role == AgentRole.RED_TEAM:
            # Disagreement without blocking demo synthesis (MEDIUM, no hard reject).
            return {
                "recommended_reject": False,
                "summary": "Scaling claims remain weakly supported; treat industrial GPa as unproven.",
                "attacks": [
                    {
                        "description": "Extrapolating lab tensile strength to plant-scale fiber without process equivalence.",
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
        return {"summary": f"Mock noop for role {role.value}", "notes": request.messages[-1].content[:200]}
