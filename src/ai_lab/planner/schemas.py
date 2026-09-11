"""Known TaskGraph output schemas, tools, and forbidden proposal keys.

Planner input is untrusted. These registries are the only allowed identifiers.
"""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, TaskKind
from ai_lab.tools.registry import DEFAULT_TRUST

# JSON object keys that must never appear in a planner proposal (any nesting).
# TaskSpec describes work, not a command to execute.
FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "command",
        "script",
        "python_code",
        "shell",
        "cwd",
        "path",
        "eval",
        "exec",
        "subprocess",
        "import",
        "os.system",
        "bash",
        "powershell",
        "review_bundle_path",  # runtime-owned filesystem pointer
        "use_model",
        "routing_policy",
        "model_routing",
        "routing_policy_version",
        "independence_policy",
        "docker_args",
        "host_path",
        "network_proxy",
        "environment_mutation",
        "image",
        "docker_image",
        "privileged",
        "cap_add",
        "mounts",
        "devices",
        "entrypoint",
        "runtime",
        "container_name",
        "api_key",
        "api_keys",
        "run_budget",
        "host_cwd",
        "solver_import",
        "python_import",
        "docker_host",
        "sandbox_policy",
        "network_mode",
    }
)

# Tools an LLM proposal may name. Unknown names fail validation (no dynamic import).
KNOWN_TOOL_NAMES = frozenset(DEFAULT_TRUST.keys())

# Always-legal input ids (project files / runtime freeze points — not future task outputs).
KNOWN_ARTIFACT_IDS = frozenset(
    {
        "problem.md",
        "requirements.md",
        "assumptions.md",
        "review_bundle",
        "reviews/review_bundle.json",
        "models/uniaxial_tension.json",
    }
)

# output_schema → which roles / task kinds may produce it
_AGENT = TaskKind.AGENT
_CHECK = TaskKind.DETERMINISTIC_CHECK
_ADJ = TaskKind.ADJUDICATION

OUTPUT_SCHEMAS: dict[str, frozenset[AgentRole | TaskKind]] = {
    "agent_result": frozenset({_AGENT}),
    "problem_framing": frozenset({AgentRole.CHIEF_ENGINEER}),
    "decomposition": frozenset({AgentRole.CHIEF_ENGINEER}),
    "research_findings": frozenset({AgentRole.RESEARCH}),
    "hypotheses": frozenset({AgentRole.THEORIST}),
    "analysis": frozenset({AgentRole.THEORIST}),
    "computation": frozenset({AgentRole.SIMULATION}),
    "check_report": frozenset({_CHECK}),
    "verification_report": frozenset({AgentRole.VERIFICATION}),
    "red_team_report": frozenset({AgentRole.RED_TEAM}),
    "adjudication_result": frozenset({_ADJ}),
    "synthesis_bundle": frozenset({AgentRole.CHIEF_ENGINEER}),
    "simulation_spec": frozenset({TaskKind.MODEL_BUILD}),
    "simulation_result": frozenset({TaskKind.SIMULATION}),
    "simulation_verification": frozenset({TaskKind.SIMULATION_VERIFICATION}),
}

# Roles whose workspace must not be an input to independent review tasks.
AUTHOR_ROLES = frozenset(
    {
        AgentRole.CHIEF_ENGINEER,
        AgentRole.RESEARCH,
        AgentRole.THEORIST,
        AgentRole.SIMULATION,
        AgentRole.ENGINEERING_DESIGNER,
        AgentRole.EXPERIMENTAL_SCIENTIST,
    }
)

REVIEW_ROLES = frozenset({AgentRole.VERIFICATION, AgentRole.RED_TEAM})

INDEPENDENT_REVIEW_GROUP = "independent_review"


def schema_allows(output_schema: str, *, role: AgentRole | None, kind: TaskKind) -> bool:
    """True if this role/kind is allowed to emit the named schema."""
    allowed = OUTPUT_SCHEMAS.get(output_schema)
    if allowed is None:
        return False
    if kind == TaskKind.AGENT:
        if role is None:
            return False
        if _AGENT in allowed:
            return True
        return role in allowed
    return kind in allowed
