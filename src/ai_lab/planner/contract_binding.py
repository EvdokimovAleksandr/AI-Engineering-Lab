"""Contract binding helpers for TaskGraph validation (PR-04)."""

from __future__ import annotations

from ai_lab.core.enums import ContractStatus, TaskKind
from ai_lab.core.models import TaskGraph, TaskSpec

# Узлы, которые могут произвести quantitative required_outputs.
_CALC_PRODUCER_KINDS = frozenset(
    {
        TaskKind.SIMULATION,
        TaskKind.MODEL_BUILD,
        TaskKind.SIMULATION_VERIFICATION,
    }
)
_CALC_PRODUCER_IDS = frozenset(
    {"calculation", "simulation", "deterministic_verify", "model_build"}
)


def task_contract_version(task: TaskSpec, graph: TaskGraph) -> str | None:
    """Per-task stamp, else graph-level stamp."""
    meta = task.metadata or {}
    if meta.get("contract_version"):
        return str(meta["contract_version"])
    gmeta = graph.metadata or {}
    if gmeta.get("contract_version"):
        return str(gmeta["contract_version"])
    return None


def task_investigation_id(task: TaskSpec, graph: TaskGraph) -> str | None:
    meta = task.metadata or {}
    if meta.get("investigation_id"):
        return str(meta["investigation_id"])
    gmeta = graph.metadata or {}
    if gmeta.get("investigation_id"):
        return str(gmeta["investigation_id"])
    return None


def graph_has_calculation_producer(graph: TaskGraph) -> bool:
    for task in graph.tasks:
        if task.task_kind in _CALC_PRODUCER_KINDS:
            return True
        if task.task_id in _CALC_PRODUCER_IDS:
            return True
        if task.role is not None and task.role.value == "simulation":
            return True
    return False


def contract_binding_errors(
    graph: TaskGraph,
    *,
    contract_version: str | None,
    contract_status: ContractStatus | None,
    investigation_id: str | None,
    required_outputs: list[str] | None = None,
    require_calculation_producers: bool = False,
) -> list[str]:
    """Deterministic contract↔graph checks. Empty list = OK."""
    if not contract_version:
        return []

    errors: list[str] = []
    g_cv = (graph.metadata or {}).get("contract_version")
    if g_cv not in (None, "", contract_version):
        errors.append(
            f"graph metadata contract_version {g_cv!r} != active {contract_version!r}"
        )

    locked = contract_status == ContractStatus.LOCKED
    for task in graph.tasks:
        t_cv = task_contract_version(task, graph)
        if t_cv is None:
            if locked:
                # Orphan: LOCKED contract требует явной привязки на каждом узле/графе.
                errors.append(
                    f"{task.task_id}: missing contract_version binding while contract is LOCKED"
                )
            continue
        if t_cv != contract_version:
            errors.append(
                f"{task.task_id}: contract_version {t_cv!r} != active {contract_version!r}"
            )
        if investigation_id:
            t_inv = task_investigation_id(task, graph)
            if t_inv not in (None, "", investigation_id):
                errors.append(
                    f"{task.task_id}: investigation_id {t_inv!r} != {investigation_id!r}"
                )

    # Soft→hard: calculation-heavy profiles with named required_outputs need a producer.
    outputs = list(required_outputs or [])
    if require_calculation_producers and outputs and not graph_has_calculation_producer(graph):
        errors.append(
            "contract required_outputs need a calculation/simulation producer task; "
            f"missing for {outputs}"
        )
    return errors
