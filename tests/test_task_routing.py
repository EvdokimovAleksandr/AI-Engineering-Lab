"""Deterministic Task Router tests — policy wins over unsafe classifier proposals."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.core.models import LabConfig
from ai_lab.planner.static import StaticPlanner
from ai_lab.task_routing.classifier import FixedTaskClassifier, HeuristicTaskClassifier
from ai_lab.task_routing.enums import EvidenceRequirement, WorkflowProfile
from ai_lab.task_routing.models import TaskClassification
from ai_lab.task_routing.policy import (
    TaskRoutingPolicy,
    default_rules,
    task_routing_policy_from_config,
)
from ai_lab.task_routing.router import TaskRouter

REPO = Path(__file__).resolve().parents[1]


def _ctx(project_id: str, problem: str):
    # Lightweight stand-in — avoids importing planner package during collection.
    return type(
        "Ctx",
        (),
        {
            "project_id": project_id,
            "run_id": "run_test",
            "problem_text": problem,
            "requirements_text": "",
            "assumptions_text": "",
            "extra_data": {},
        },
    )()


def _policy() -> TaskRoutingPolicy:
    raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    return task_routing_policy_from_config(LabConfig.model_validate(raw))


def test_simple_heater_routes_to_simple() -> None:
    problem = (REPO / "benchmarks" / "simple_heater" / "problem.md").read_text(encoding="utf-8")
    decision = TaskRouter(_policy()).route(_ctx("simple_heater", problem))
    assert decision.final_workflow == WorkflowProfile.SIMPLE
    assert EvidenceRequirement.DETERMINISTIC in decision.final_evidence
    assert decision.require_red_team is False
    assert "research" not in {t.task_id for t in StaticPlanner(pipeline="simple")._tasks()}


def test_shaft_design_routes_to_standard() -> None:
    problem = (REPO / "benchmarks" / "shaft_design" / "problem.md").read_text(encoding="utf-8")
    decision = TaskRouter(_policy()).route(_ctx("shaft_design", problem))
    assert decision.final_workflow == WorkflowProfile.STANDARD
    assert decision.require_independent_review is True


def test_spider_silk_routes_to_research() -> None:
    problem = (REPO / "benchmarks" / "spider_silk_review" / "problem.md").read_text(
        encoding="utf-8"
    )
    decision = TaskRouter(_policy()).route(_ctx("spider_silk_review", problem))
    assert decision.final_workflow == WorkflowProfile.RESEARCH
    tasks = {t.task_id for t in StaticPlanner(pipeline="research")._tasks()}
    assert "research" in tasks and "red_team" in tasks


def test_high_risk_simple_task_cannot_bypass_safety_policy() -> None:
    """Complexity low + risk HIGH → must not stay on SIMPLE."""
    unsafe = TaskClassification(
        task_type="simple_calc",
        domain="electrical",
        complexity=1,
        risk=8,
        uncertainty=1,
        required_evidence=[EvidenceRequirement.DETERMINISTIC],
        recommended_workflow=WorkflowProfile.SIMPLE,
        reasoning="Classifier wrongly proposed SIMPLE for a hazardous system",
        confidence=0.9,
    )
    decision = TaskRouter(_policy(), classifier=FixedTaskClassifier(unsafe)).route(
        _ctx("hazard_heater", "safety-critical high-voltage heater sizing")
    )
    assert decision.final_workflow != WorkflowProfile.SIMPLE
    assert WORKFLOW_AT_LEAST(decision.final_workflow, WorkflowProfile.COMPLEX)
    assert EvidenceRequirement.VERIFICATION_PLUS_REDTEAM in decision.final_evidence
    assert decision.policy_escalated is True


def test_high_uncertainty_increases_evidence_requirements() -> None:
    proposal = TaskClassification(
        task_type="open_problem",
        domain="materials",
        complexity=4,
        risk=2,
        uncertainty=8,
        required_evidence=[EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION],
        recommended_workflow=WorkflowProfile.STANDARD,
        reasoning="Uncertain literature",
        confidence=0.5,
    )
    decision = TaskRouter(_policy(), classifier=FixedTaskClassifier(proposal)).route(
        _ctx("uncertain", "uncertain contradictory sparse evidence")
    )
    assert decision.final_workflow == WorkflowProfile.RESEARCH
    assert EvidenceRequirement.RESEARCH_PLUS_VERIFICATION in decision.final_evidence


def test_llm_cannot_lower_policy_mandated_workflow() -> None:
    """Classifier proposes SIMPLE; policy floors from complexity band force raise."""
    proposal = TaskClassification(
        task_type="hard",
        domain="general",
        complexity=9,
        risk=2,
        uncertainty=2,
        required_evidence=[EvidenceRequirement.DETERMINISTIC],
        recommended_workflow=WorkflowProfile.SIMPLE,
        reasoning="Unsafe under-route",
        confidence=0.99,
    )
    decision = TaskRouter(_policy(), classifier=FixedTaskClassifier(proposal)).route(
        _ctx("underroute", "complex multi-stage research problem")
    )
    assert decision.final_workflow == WorkflowProfile.RESEARCH
    assert decision.policy_escalated is True


def test_deterministic_policy_wins_over_unsafe_llm_classification() -> None:
    proposal = TaskClassification(
        task_type="critical",
        domain="nuclear_adj",
        complexity=2,
        risk=9,
        uncertainty=1,
        required_evidence=[EvidenceRequirement.DETERMINISTIC],
        recommended_workflow=WorkflowProfile.SIMPLE,
        reasoning="LLM tried to skip review",
        confidence=1.0,
    )
    decision = TaskRouter(_policy(), classifier=FixedTaskClassifier(proposal)).route(
        _ctx("critical", "nuclear safety-critical calculation")
    )
    assert decision.final_workflow != WorkflowProfile.SIMPLE
    assert EvidenceRequirement.HUMAN_REVIEW in decision.final_evidence
    assert decision.require_hitl is True


def test_simple_tasks_do_not_trigger_full_lab_workflow() -> None:
    problem = (REPO / "benchmarks" / "simple_heater" / "problem.md").read_text(encoding="utf-8")
    decision = TaskRouter(_policy()).route(_ctx("simple_heater", problem))
    assert decision.final_workflow == WorkflowProfile.SIMPLE
    tasks = {t.task_id for t in StaticPlanner(pipeline="simple")._tasks()}
    assert "research" not in tasks
    assert "hypothesis" not in tasks
    assert "red_team" not in tasks
    assert "verification" not in tasks
    assert "calculation" in tasks and "deterministic_verify" in tasks


def WORKFLOW_AT_LEAST(observed: WorkflowProfile, floor: WorkflowProfile) -> bool:
    order = [
        WorkflowProfile.SIMPLE,
        WorkflowProfile.STANDARD,
        WorkflowProfile.COMPLEX,
        WorkflowProfile.RESEARCH,
    ]
    return order.index(observed) >= order.index(floor)


def test_default_rules_loaded_from_config() -> None:
    policy = _policy()
    assert policy.enabled is True
    assert len(policy.rules) == len(default_rules())


def test_heuristic_classifier_smoke() -> None:
    clf = HeuristicTaskClassifier()
    c = clf.classify(_ctx("x", "Calculate heater power for 20 liters of water"))
    assert c.recommended_workflow == WorkflowProfile.SIMPLE
