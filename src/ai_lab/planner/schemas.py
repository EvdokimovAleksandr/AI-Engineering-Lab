"""Known TaskGraph output schemas, tools, and forbidden proposal keys.

Planner input is untrusted. These registries are the only allowed identifiers.
"""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, TaskKind
from ai_lab.tools.registry import DEFAULT_TRUST

# Closed-world identifiers. Prompt, schema, and validator must all read these —
# do not copy role/kind lists by hand in other modules.
def allowed_role_values() -> tuple[str, ...]:
    """Canonical AgentRole values. LLM may not invent names outside this tuple."""
    return tuple(role.value for role in AgentRole)


def allowed_task_kind_values() -> tuple[str, ...]:
    """Canonical TaskKind values. LLM may not invent kinds outside this tuple."""
    return tuple(kind.value for kind in TaskKind)


def allowed_output_schema_values() -> tuple[str, ...]:
    return tuple(OUTPUT_SCHEMAS.keys())


def planner_contract_text() -> str:
    """Machine-readable contract injected into the planner prompt (not authority)."""
    roles = ", ".join(allowed_role_values())
    kinds = ", ".join(allowed_task_kind_values())
    schemas = ", ".join(allowed_output_schema_values())
    return (
        "You are proposing a plan inside an existing fixed agent architecture.\n"
        "You do not invent roles. You do not invent tools. You do not invent task kinds.\n"
        "You do not change security policy, sandbox policy, or budget policy.\n"
        "You may only select from the provided enums and schemas.\n"
        "Return only the requested structured object.\n\n"
        f"Allowed roles: {roles}\n"
        f"Allowed task_kind: {kinds}\n"
        f"Allowed output_schema (string, not a JSON Schema object): {schemas}\n"
        "inputs: array of strings (artifact or task ids), never an object.\n"
        "depends_on: array of task_id strings.\n"
        "budget_slice: omit (preferred) or object "
        "{max_agent_calls, max_tool_calls, max_tokens, max_cost} — never a number.\n"
        "Forbidden: command, script, shell, cwd, path, review_bundle_path, "
        "routing_policy, api_key, sandbox_policy, docker_args, host_path.\n"
    )

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
