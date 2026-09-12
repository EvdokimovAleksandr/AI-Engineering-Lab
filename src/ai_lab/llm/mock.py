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
        # Adversarial / positive calculation fixtures (V2.6 regression).
        simulation_fixture: str | None = None,
    ) -> None:
        self.force_verification_fail = force_verification_fail
        self.verification_status_override = verification_status_override
        self.simulation_fixture = simulation_fixture
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
            # Problem-bound outputs: heater fixtures / heater problem text lock power;
            # default silk-style problems lock stress.
            user_blob = " ".join(
                str(getattr(m, "content", m) if not isinstance(m, dict) else m.get("content", ""))
                for m in (request.messages or [])
            ).lower()
            heaterish = (
                (self.simulation_fixture or "").startswith("heater")
                or (self.simulation_fixture or "").startswith("irrelevant_")
                or self.simulation_fixture
                in {
                    "kv_cache_unrelated",
                    "correct_prose_wrong_compute",
                    "wrong_inputs",
                    "wrong_units",
                    "wrong_formula",
                    "policy_lock_attack",
                    "aluminum_expansion",
                    "heat_flux",
                    "empty_verification_path",
                }
                or "heater" in user_blob
                or "water" in user_blob
                or "нагре" in user_blob
            )
            if heaterish:
                required_outputs = ["power"]
                expected_dimensions = {"power": "W"}
                understanding = (
                    "Closed-form heater power for heating a known water mass over a fixed time."
                )
            else:
                required_outputs = ["stress_gpa"]
                expected_dimensions = {"stress_gpa": "GPa"}
                understanding = (
                    "Industrial spider silk requires host expression and fiber spinning."
                )
            return {
                "summary": "Decomposed problem into research, analysis, and verification tracks.",
                "understanding": understanding,
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
                "required_outputs": required_outputs,
                "expected_dimensions": expected_dimensions,
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
            # Fixture override for adversarial / heater regression tests.
            fixture = self.simulation_fixture or (request.metadata or {}).get(
                "simulation_fixture"
            )
            if fixture == "heater_correct":
                return _heater_correct_payload()
            if fixture == "heater_wrong_math":
                return _heater_wrong_math_payload()
            if fixture == "kv_cache_unrelated":
                return _kv_cache_unrelated_payload()
            if fixture == "irrelevant_aluminum":
                return _aluminum_expansion_payload()
            if fixture == "irrelevant_heat_flux":
                return _heat_flux_payload()
            if fixture == "wrong_inputs":
                return _wrong_inputs_payload()
            if fixture == "wrong_units":
                return _wrong_units_payload()
            if fixture == "wrong_formula":
                return _wrong_formula_payload()
            if fixture == "correct_prose_wrong_compute":
                return _correct_prose_wrong_compute_payload()
            if fixture == "policy_lock_attack":
                return _policy_lock_attack_payload()
            if fixture == "accidental_numeric_match":
                return _accidental_numeric_match_payload()
            return {
                "calculation_spec": {
                    "objective": "calculate_fiber_stress",
                    "required_inputs": ["force_n", "diameter_m"],
                    "required_outputs": ["stress_gpa"],
                    "expected_dimensions": {"stress_gpa": "GPa"},
                    "expected_relations": ["stress = F / A"],
                    "domain": "mechanics",
                },
                "code": (
                    "import math\n"
                    "diameter_m = 5.0e-6\n"
                    "force_n = 0.05\n"
                    "area_m2 = math.pi * (diameter_m / 2) ** 2\n"
                    "stress_gpa = (force_n / area_m2) / 1e9\n"
                    "print(round(stress_gpa, 6))\n"
                ),
                "declared_outputs": {
                    "stress_gpa": {"value": round(expected, 6), "unit": "GPa"},
                },
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


def _heater_correct_payload() -> dict[str, Any]:
    """Correct closed-form heater power (~3.25 kW with 15% losses)."""
    # Q = m c ΔT; m=20kg, c=4180, dT=60 → 5.016e6 J; t=1800s → 2786.7 W; +15% → 3204.7 W
    power_w = 3204.666666666667
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": [
                "water_volume_l",
                "initial_temperature_c",
                "target_temperature_c",
                "heating_time_s",
                "loss_fraction",
            ],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "expected_relations": ["Q = m * c * dT", "P = Q / t * (1 + losses)"],
            "domain": "thermal_heating",
        },
        "code": (
            "m_kg = 20.0\n"
            "c = 4180.0\n"
            "dT = 60.0\n"
            "t_s = 1800.0\n"
            "loss = 0.15\n"
            "Q = m_kg * c * dT\n"
            "power = (Q / t_s) * (1.0 + loss)\n"
            "print(power)\n"
        ),
        "declared_outputs": {"power": {"value": power_w, "unit": "W"}},
        "claims": [
            {
                "statement": f"Required heater power is approximately {power_w:.0f} W (~3.2 kW).",
                "kind": "CALCULATION",
                "assumptions": ["cp=4180 J/(kg·K)", "density≈1 kg/L", "uniform losses 15%"],
                "falsifiers": ["Measured energy draw differs by >20%"],
                "math_check": {
                    "expression": "(m_kg * c * dT / t_s) * (1.0 + loss)",
                    "expected": power_w,
                    "tolerance": 1.0,
                    "inputs": {
                        "m_kg": 20.0,
                        "c": 4180.0,
                        "dT": 60.0,
                        "t_s": 1800.0,
                        "loss": 0.15,
                    },
                    "units": {
                        "m_kg": "kg",
                        "c": "J/(kg*K)",
                        "dT": "K",
                        "t_s": "s",
                        "loss": "",
                    },
                },
            }
        ],
    }


def _heater_wrong_math_payload() -> dict[str, Any]:
    """Same contract and correct code; planted wrong *expected* so verifier FAILs.

    Keep the correct sandbox code: attaching a wrong print that matches the
    planted expected would make independent recompute falsely PASS.
    """
    payload = _heater_correct_payload()
    wrong = 4100.0
    payload["declared_outputs"] = {"power": {"value": wrong, "unit": "W"}}
    payload["claims"][0]["statement"] = f"Required heater power is {wrong:.0f} W."
    payload["claims"][0]["math_check"]["expected"] = wrong
    return payload


def _kv_cache_unrelated_payload() -> dict[str, Any]:
    """Adversarial: heater contract proposed, but computation is KV-cache memory."""
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": [
                "water_volume_l",
                "initial_temperature_c",
                "target_temperature_c",
                "heating_time_s",
                "loss_fraction",
            ],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "expected_relations": ["Q = m * c * dT", "P = Q / t"],
            "domain": "thermal_heating",
        },
        "code": (
            "# KV-cache memory estimate for batched LLM inference\n"
            "batch = 8\n"
            "seq = 4096\n"
            "layers = 32\n"
            "heads = 32\n"
            "dim = 128\n"
            "bytes_per = 2\n"
            "memory_gib = 4.0\n"
            "print(memory_gib)\n"
        ),
        "declared_outputs": {"memory_gib": {"value": 4.0, "unit": "GiB"}},
        "claims": [
            {
                "statement": "KV-cache = 4.0 GiB",
                "kind": "CALCULATION",
                "assumptions": [],
                "falsifiers": [],
            }
        ],
    }


def _aluminum_expansion_payload() -> dict[str, Any]:
    """Semantic drift: aluminum rod thermal expansion instead of heater power."""
    return {
        "calculation_spec": {
            "objective": "aluminum_rod_thermal_expansion",
            "required_inputs": ["L0_m", "alpha", "dT"],
            "required_outputs": ["delta_L"],
            "expected_dimensions": {"delta_L": "m"},
            "domain": "thermal_expansion",
        },
        "code": (
            "L0 = 1.0\n"
            "alpha = 23e-6\n"
            "dT = 60.0\n"
            "delta_L = L0 * alpha * dT\n"
            "print(delta_L)\n"
        ),
        "declared_outputs": {"delta_L": {"value": 0.00138, "unit": "m"}},
        "claims": [
            {
                "statement": "Aluminum rod extension delta_L ≈ 1.38 mm",
                "kind": "CALCULATION",
                "covers_outputs": ["delta_L"],
            }
        ],
    }


def _heat_flux_payload() -> dict[str, Any]:
    """Semantic drift: conduction heat flux instead of heater electrical power."""
    return {
        "calculation_spec": {
            "objective": "thermal_conduction_heat_flux",
            "required_inputs": ["k", "dT", "L"],
            "required_outputs": ["heat_flux"],
            "expected_dimensions": {"heat_flux": "W/m**2"},
            "domain": "heat_transfer",
        },
        "code": (
            "k = 0.6\n"
            "dT = 60.0\n"
            "L = 0.01\n"
            "heat_flux = k * dT / L\n"
            "print(heat_flux)\n"
        ),
        "declared_outputs": {"heat_flux": {"value": 3600.0, "unit": "W/m**2"}},
        "claims": [
            {
                "statement": "Conduction heat_flux = 3600 W/m^2",
                "kind": "CALCULATION",
                "covers_outputs": ["heat_flux"],
            }
        ],
    }


def _wrong_inputs_payload() -> dict[str, Any]:
    """Self-consistent but wrong mass (2 kg instead of 20 kg)."""
    power_w = 320.4666666666667  # 10× too small
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": ["m_kg", "dT", "t_s", "loss"],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "domain": "thermal_heating",
        },
        "code": (
            "m_kg = 2.0\n"
            "c = 4180.0\n"
            "dT = 60.0\n"
            "t_s = 1800.0\n"
            "loss = 0.15\n"
            "power = (m_kg * c * dT / t_s) * (1.0 + loss)\n"
            "print(power)\n"
        ),
        "declared_outputs": {"power": {"value": power_w, "unit": "W"}},
        "claims": [
            {
                "statement": f"Required heater power is approximately {power_w:.0f} W.",
                "kind": "CALCULATION",
                "covers_outputs": ["power"],
                "math_check": {
                    "expression": "(m_kg * c * dT / t_s) * (1.0 + loss)",
                    "expected": power_w,
                    "tolerance": 1.0,
                    "inputs": {
                        "m_kg": 2.0,
                        "c": 4180.0,
                        "dT": 60.0,
                        "t_s": 1800.0,
                        "loss": 0.15,
                    },
                    "units": {
                        "m_kg": "kg",
                        "c": "J/(kg*K)",
                        "dT": "K",
                        "t_s": "s",
                        "loss": "",
                    },
                },
            }
        ],
    }


def _wrong_units_payload() -> dict[str, Any]:
    """Declares energy (J) as if it were heater electrical power."""
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": ["m_kg", "dT"],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "domain": "thermal_heating",
        },
        "code": (
            "m_kg = 20.0\n"
            "c = 4180.0\n"
            "dT = 60.0\n"
            "Q = m_kg * c * dT\n"
            "print(Q)\n"
        ),
        "declared_outputs": {"power": {"value": 5016000.0, "unit": "J"}},
        "claims": [
            {
                "statement": "Required heater power is 5016000 J",
                "kind": "CALCULATION",
                "covers_outputs": ["power"],
            }
        ],
    }


def _wrong_formula_payload() -> dict[str, Any]:
    """Omits loss factor: P = mcΔT/t without (1+loss) or /(1-loss)."""
    power_w = 2786.666666666667
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": ["m_kg", "dT", "t_s", "loss"],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "domain": "thermal_heating",
        },
        "code": (
            "m_kg = 20.0\n"
            "c = 4180.0\n"
            "dT = 60.0\n"
            "t_s = 1800.0\n"
            "power = m_kg * c * dT / t_s\n"
            "print(power)\n"
        ),
        "declared_outputs": {"power": {"value": power_w, "unit": "W"}},
        "claims": [
            {
                "statement": f"Required heater power is approximately {power_w:.0f} W.",
                "kind": "CALCULATION",
                "covers_outputs": ["power"],
                "math_check": {
                    "expression": "m_kg * c * dT / t_s",
                    "expected": power_w,
                    "tolerance": 1.0,
                    "inputs": {
                        "m_kg": 20.0,
                        "c": 4180.0,
                        "dT": 60.0,
                        "t_s": 1800.0,
                        "loss": 0.0,
                    },
                    "units": {
                        "m_kg": "kg",
                        "c": "J/(kg*K)",
                        "dT": "K",
                        "t_s": "s",
                        "loss": "",
                    },
                },
            }
        ],
    }


def _correct_prose_wrong_compute_payload() -> dict[str, Any]:
    """Prose claims ~3.2 kW heater power; artifact is KV-cache."""
    payload = _kv_cache_unrelated_payload()
    payload["claims"] = [
        {
            "statement": "Required heater power = 3.25 kW",
            "kind": "CALCULATION",
            "covers_outputs": ["power"],
        }
    ]
    return payload


def _policy_lock_attack_payload() -> dict[str, Any]:
    """Tries to drop locked power output and override dimension to GiB."""
    return {
        "calculation_spec": {
            "objective": "calculate_anything",
            "required_outputs": ["engineering_result"],
            "expected_dimensions": {"power": "GiB", "engineering_result": "1"},
            "minimum_checks": 0,
            "verification_required": False,
            "domain": "gaming",
        },
        "code": "memory_gib = 4.0\nprint(memory_gib)\n",
        "declared_outputs": {"memory_gib": {"value": 4.0, "unit": "GiB"}},
        "claims": [
            {
                "statement": "engineering_result done",
                "kind": "CALCULATION",
            }
        ],
    }


def _accidental_numeric_match_payload() -> dict[str, Any]:
    """~3.2 kW by accident with wrong mass and delta-T."""
    # m=10, dT=120 → same Q as m=20,dT=60; with loss → ~3204 W
    power_w = 3204.666666666667
    return {
        "calculation_spec": {
            "objective": "calculate_heater_power",
            "required_inputs": ["m_kg", "dT", "t_s", "loss"],
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
            "domain": "thermal_heating",
        },
        "code": (
            "m_kg = 10.0\n"
            "c = 4180.0\n"
            "dT = 120.0\n"
            "t_s = 1800.0\n"
            "loss = 0.15\n"
            "power = (m_kg * c * dT / t_s) * (1.0 + loss)\n"
            "print(power)\n"
        ),
        "declared_outputs": {"power": {"value": power_w, "unit": "W"}},
        "claims": [
            {
                "statement": f"Required heater power is approximately {power_w:.0f} W.",
                "kind": "CALCULATION",
                "covers_outputs": ["power"],
                "math_check": {
                    "expression": "(m_kg * c * dT / t_s) * (1.0 + loss)",
                    "expected": power_w,
                    "tolerance": 1.0,
                    "inputs": {
                        "m_kg": 10.0,
                        "c": 4180.0,
                        "dT": 120.0,
                        "t_s": 1800.0,
                        "loss": 0.15,
                    },
                    "units": {
                        "m_kg": "kg",
                        "c": "J/(kg*K)",
                        "dT": "K",
                        "t_s": "s",
                        "loss": "",
                    },
                },
            }
        ],
    }


