"""Engineering Simulation Framework: units, physics, validation, determinism."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_lab.checks.units import convert_to, parse_quantity, to_pint
from ai_lab.checks.verifier import DeterministicVerifier
from ai_lab.core.enums import (
    CheckStatus,
    EvidenceKind,
    ParameterTrust,
    ProjectState,
    SimulationStatus,
    TaskGraphValidationReason,
    TaskKind,
)
from ai_lab.core.models import LabConfig, Quantity, TaskGraph, TaskSpec
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.planner.static import tensile_pipeline_tasks
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph
from ai_lab.simulation.claims import verification_specs_from_simulation
from ai_lab.simulation.errors import UnknownSolverError
from ai_lab.simulation.load import load_trusted_spec, spec_from_fixture
from ai_lab.simulation.models import (
    Assumption,
    EquationSpec,
    NumericalSettings,
    SimulationSpec,
    SolverConfig,
)
from ai_lab.simulation.pipeline import run_simulation
from ai_lab.simulation.protocol import SolverContext
from ai_lab.simulation.registry import SolverRegistry, default_registry
from ai_lab.simulation.validate import validate_simulation_spec

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "simulation" / "uniaxial_tension.json"


def _spec_from_disk() -> SimulationSpec:
    return load_trusted_spec("uniaxial_tension", repo_root=REPO)


def _ctx() -> SolverContext:
    return SolverContext(run_id="run_test", task_id="sim")


def test_golden_area_from_5_um() -> None:
    spec = _spec_from_disk()
    result = run_simulation(spec, _ctx())
    assert result.status == SimulationStatus.SUCCESS
    area = to_pint(result.outputs["area"])
    expected = parse_quantity(1.963495408493621e-11, "m**2")
    aligned = convert_to(area, expected)
    assert abs(float(aligned.magnitude) - float(expected.magnitude)) <= 1e-20


def test_mpa_gpa_not_string_equality() -> None:
    a = parse_quantity(50, "MPa")
    b = parse_quantity(0.05, "GPa")
    aligned = convert_to(a, b)
    assert abs(float(aligned.magnitude) - 0.05) < 1e-12
    assert "50 MPa" != "0.05 GPa"


def test_sigma_equals_f_over_a() -> None:
    spec = _spec_from_disk()
    result = run_simulation(spec, _ctx())
    stress = to_pint(result.outputs["stress"])
    force = to_pint(spec.parameters["force"])
    area = to_pint(result.outputs["area"])
    aligned = convert_to(force / area, stress)
    assert abs(float(aligned.magnitude) - float(stress.magnitude)) <= 1e-6


def test_epsilon_and_hooke() -> None:
    spec = _spec_from_disk()
    result = run_simulation(spec, _ctx())
    strain = float(to_pint(result.outputs["strain"]).to_base_units().magnitude)
    assert abs(strain - 0.01) < 1e-12
    elastic = to_pint(result.outputs["elastic_prediction"])
    stress = to_pint(result.outputs["stress"])
    aligned = convert_to(elastic, stress)
    assert abs(float(aligned.magnitude) - float(stress.magnitude)) <= 1e-4


def test_failure_when_stress_exceeds_strength() -> None:
    spec = _spec_from_disk()
    params = dict(spec.parameters)
    params["force"] = Quantity(value=0.2, unit="N")
    overloaded = spec.model_copy(update={"parameters": params, "metadata": {"fail_if_above_strength": True}})
    result = run_simulation(overloaded, _ctx())
    assert result.status == SimulationStatus.OUT_OF_DOMAIN
    assert any(c.name == "failure_condition" and not c.passed for c in result.checks)


def test_negative_diameter_rejected() -> None:
    spec = _spec_from_disk()
    params = dict(spec.parameters)
    params["diameter"] = Quantity(value=-1, unit="micrometer")
    bad = spec.model_copy(update={"parameters": params})
    result = run_simulation(bad, _ctx())
    assert result.status == SimulationStatus.INVALID_PARAMETERS


def test_zero_modulus_rejected() -> None:
    spec = _spec_from_disk()
    params = dict(spec.parameters)
    params["youngs_modulus"] = Quantity(value=0, unit="GPa")
    bad = spec.model_copy(update={"parameters": params})
    result = run_simulation(bad, _ctx())
    assert result.status == SimulationStatus.INVALID_PARAMETERS


def test_incompatible_force_unit() -> None:
    spec = _spec_from_disk()
    params = dict(spec.parameters)
    params["force"] = Quantity(value=1, unit="m")
    bad = spec.model_copy(update={"parameters": params})
    result = run_simulation(bad, _ctx())
    assert result.status == SimulationStatus.INVALID_PARAMETERS


def test_forbidden_equation_import() -> None:
    spec = _spec_from_disk()
    eqs = list(spec.equations) + [
        EquationSpec(name="evil", expression="__import__('os').system('id')")
    ]
    bad = spec.model_copy(update={"equations": eqs})
    validation = validate_simulation_spec(bad)
    assert validation.ok is False


def test_assumption_cannot_be_fact() -> None:
    with pytest.raises(ValueError, match="FACT"):
        Assumption(id="x", statement="iso", kind=EvidenceKind.FACT)


def test_unknown_solver_rejected() -> None:
    spec = _spec_from_disk()
    with pytest.raises(Exception):
        SolverConfig(solver_id="../../evil")
    with pytest.raises(UnknownSolverError):
        default_registry().get("not_a_solver")
    bad = spec.model_copy(update={"solver": SolverConfig(solver_id="uniaxial_tension")})
    # Registry rejects unknown ids at get(); spec with unregistered id fails validate.
    from ai_lab.simulation.models import SolverConfig as SC

    sneaky = spec.model_copy(update={"solver": SC(solver_id="evil_solver")})
    v = validate_simulation_spec(sneaky)
    assert v.ok is False


def test_determinism_same_spec_same_outputs() -> None:
    spec = _spec_from_disk()
    a = run_simulation(spec, _ctx())
    b = run_simulation(spec, SolverContext(run_id="run_test", task_id="sim2"))
    assert a.status == b.status == SimulationStatus.SUCCESS
    for key in a.outputs:
        assert a.outputs[key].value == b.outputs[key].value
        assert a.outputs[key].unit == b.outputs[key].unit
    assert a.provenance.get("solver_id") == b.provenance.get("solver_id")


def test_fixture_is_stub_not_fact() -> None:
    spec = _spec_from_disk()
    for prov in spec.parameter_provenance.values():
        assert prov.source == "fixture://synthetic"
        assert prov.trust == ParameterTrust.STUB


def test_golden_verification_passes() -> None:
    spec = _spec_from_disk()
    result = run_simulation(spec, _ctx())
    engine = DeterministicVerifier()
    specs = verification_specs_from_simulation(spec, result)
    # σ ≈ Eε is included; for this fixture it should PASS.
    for vs in specs:
        if vs.metadata.get("output") == "stress_vs_E_strain":
            vr = engine.verify(vs)
            assert vr.status == CheckStatus.PASS
        elif vs.metadata.get("output") in {"area", "stress", "strain", "elastic_prediction", "mass", "failure_margin"}:
            vr = engine.verify(vs)
            assert vr.status == CheckStatus.PASS, (vs.metadata, vr.diagnostics)


def test_missing_assumptions_invalid_model() -> None:
    spec = _spec_from_disk()
    bare = spec.model_copy(update={"assumptions": []})
    result = run_simulation(bare, _ctx())
    assert result.status == SimulationStatus.INVALID_MODEL


def test_spec_rejects_docker_metadata() -> None:
    spec = _spec_from_disk()
    with pytest.raises(ValueError, match="host-control"):
        SimulationSpec.model_validate({**spec.model_dump(mode="json"), "metadata": {"docker_args": "--privileged"}})


def test_planner_unknown_solver_rejected() -> None:
    tasks = tensile_pipeline_tasks()
    tasks = [
        t.model_copy(update={"metadata": {"solver_id": "../../evil"}}) if t.task_id == "run_tensile_simulation" else t
        for t in tasks
    ]
    graph = TaskGraph(graph_id="bad_solver", tasks=tasks)
    result = validate_task_graph(graph)
    assert result.ok is False
    assert result.reason == TaskGraphValidationReason.UNKNOWN_SOLVER


def test_tensile_graph_validates() -> None:
    graph = TaskGraph(graph_id="uniaxial_tension_pipeline", tasks=tensile_pipeline_tasks())
    result = validate_task_graph(graph)
    assert result.ok is True
    kinds = {t.task_id: t.task_kind for t in graph.tasks}
    assert kinds["run_tensile_simulation"] == TaskKind.SIMULATION
    assert kinds["build_tensile_model"] == TaskKind.MODEL_BUILD


@pytest.mark.asyncio
async def test_runtime_uniaxial_pipeline(tmp_path: Path) -> None:
    src = REPO / "projects" / "spider_silk_industrial"
    project_dir = tmp_path / "spider_silk_industrial"
    project_dir.mkdir()
    for name in ("problem.md", "requirements.md", "assumptions.md"):
        (project_dir / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    store = ProjectStore(project_dir)
    store.ensure_layout()
    config_raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    config_raw["provider"] = "mock"
    config_raw["runtime"]["hitl_on_disputed"] = False
    config_raw["simulation"] = {"pipeline": "uniaxial_tension", "allowed_solvers": ["uniaxial_tension"]}
    config = LabConfig.model_validate(config_raw)
    runtime = LabRuntime(store, config, repo_root=REPO, hitl=HitlGate(auto_approve=True))
    snapshot = await runtime.run()
    assert snapshot.state == ProjectState.COMPLETED
    sim = json.loads((project_dir / "simulations" / "last_result.json").read_text(encoding="utf-8"))
    assert sim["status"] == "SUCCESS"
    assert sim["scientific_status"]["physical_validity"] in {"IN_DOMAIN", "UNASSESSED"}
    comps = list((project_dir / ".runs" / snapshot.run_id / "computations").glob("*.json"))
    assert comps
    bundle = json.loads((project_dir / "reviews" / "review_bundle.json").read_text(encoding="utf-8"))
    assert bundle["computation_artifacts"]
    assert any("tensile stress" in c["statement"] for c in bundle["claims"])
