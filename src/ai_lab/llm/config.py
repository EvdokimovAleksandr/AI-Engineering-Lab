"""Structured model / routing configuration.

Typed views over LabConfig.routing / LabConfig.independence (YAML dicts).
Router selects these configs; the provider factory instantiates backends.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_lab.core.enums import AgentRole
from ai_lab.core.models import LabConfig
from ai_lab.knowledge.hashing import sha256_json

# Identifiers the factory knows how to construct. Router never switches on these.
KNOWN_PROVIDER_IDS: frozenset[str] = frozenset({"mock", "cursor_sdk", "replay"})

# Roles that may appear as routing keys besides AgentRole values.
SPECIAL_ROUTING_KEYS: frozenset[str] = frozenset(
    {"planner", "adjudication", "default", "synthesis"}
)

# Keys an LLM / research blob must never use to steer routing.
ROUTING_CONTROL_KEYS: frozenset[str] = frozenset(
    {
        "use_model",
        "routing_policy",
        "model_routing",
        "routing_policy_version",
        "independence_policy",
        "routed_provider",
    }
)

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SECRET_FRAGMENTS = ("key", "token", "secret", "password", "authorization", "api_key")


def is_safe_identifier(value: str) -> bool:
    """Reject path traversal and empty/control strings in provider/model ids."""
    if not value or not isinstance(value, str):
        return False
    if ".." in value or "/" in value or "\\" in value or "\x00" in value:
        return False
    return bool(_ID_RE.match(value))


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that look like credentials. Never persist API keys in artifacts."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).replace("-", "_").lower()
        if any(frag in lowered for frag in _SECRET_FRAGMENTS):
            continue
        if isinstance(value, dict):
            out[key] = redact_secrets(value)
        else:
            out[key] = value
    return out


class ModelConfig(BaseModel):
    """Runtime configuration for one provider/model pair.

    `metadata` may hold provider-specific non-secret parameters.
    Identity is (provider, model) — commercial names are opaque identifiers.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    endpoint: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "model")
    @classmethod
    def _safe_ids(cls, v: str) -> str:
        if not is_safe_identifier(v):
            raise ValueError(f"Invalid provider/model identifier: {v!r}")
        return v

    @field_validator("model_version")
    @classmethod
    def _safe_version(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not is_safe_identifier(v):
            raise ValueError(f"Invalid model_version: {v!r}")
        return v

    @field_validator("endpoint")
    @classmethod
    def _safe_endpoint(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if ".." in v or v.startswith(("file:", "ftp:")):
            raise ValueError(f"Invalid endpoint: {v!r}")
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError(f"endpoint must be http(s) URL, got {v!r}")
        return v

    @field_validator("temperature")
    @classmethod
    def _temp_range(cls, v: float) -> float:
        if v < 0.0 or v > 2.0:
            raise ValueError(f"temperature must be in [0, 2], got {v}")
        return v

    @field_validator("metadata")
    @classmethod
    def _no_secrets_in_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        return redact_secrets(v)

    @property
    def identity(self) -> str:
        return f"{self.provider}:{self.model}"

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic representation used for hashing (no secrets)."""
        return {
            "endpoint": self.endpoint,
            "max_tokens": self.max_tokens,
            "model": self.model,
            "model_version": self.model_version,
            "provider": self.provider,
            "temperature": self.temperature,
        }

    def identity_hash(self) -> str:
        return sha256_json(self.canonical_dict())

    def public_dump(self) -> dict[str, Any]:
        """Manifest / CLI view — identifiers only."""
        return {
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


class IndependencePolicy(BaseModel):
    """Strictness of model diversity. Loaded from YAML — never hardcoded in the router."""

    model_config = ConfigDict(extra="forbid")

    require_different_model_from_author: bool = False
    require_different_provider_between_reviewers: bool = False
    require_different_model_between_reviewers: bool = False


class RoutingPolicy(BaseModel):
    """Deterministic role → ModelConfig map. Versioned for RunManifest."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    roles: dict[str, ModelConfig] = Field(default_factory=dict)
    default: ModelConfig | None = None
    planner: ModelConfig | None = None
    adjudication: ModelConfig | None = None

    @field_validator("version")
    @classmethod
    def _version_ok(cls, v: str) -> str:
        # Versions are short labels ("1", "v1.0"), not filesystem paths.
        if not v or ".." in v or "/" in v or "\\" in v:
            raise ValueError(f"Invalid routing policy version: {v!r}")
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$", v):
            raise ValueError(f"Invalid routing policy version: {v!r}")
        return v

    def for_role(self, role: AgentRole | str) -> ModelConfig:
        """Resolve a role to a ModelConfig. Unknown roles fail (no silent default-as-unknown)."""
        key = role.value if isinstance(role, AgentRole) else str(role)
        if key == "synthesis" and key not in self.roles:
            key = AgentRole.CHIEF_ENGINEER.value
        if key in self.roles:
            return self.roles[key]
        if key == "planner" and self.planner is not None:
            return self.planner
        if key == "adjudication" and self.adjudication is not None:
            return self.adjudication
        if self.default is not None:
            return self.default
        raise KeyError(f"No model configuration for role {key!r}")

    def has_role(self, role: AgentRole | str) -> bool:
        key = role.value if isinstance(role, AgentRole) else str(role)
        if key in self.roles:
            return True
        if key == "planner" and self.planner is not None:
            return True
        if key == "adjudication" and self.adjudication is not None:
            return True
        if key == "synthesis" and (
            key in self.roles or AgentRole.CHIEF_ENGINEER.value in self.roles
        ):
            return True
        return self.default is not None

    def public_routing_map(self) -> dict[str, dict[str, Any]]:
        """role → {provider, model} for RunManifest.model_routing."""
        out: dict[str, dict[str, Any]] = {}
        for role, cfg in sorted(self.roles.items()):
            out[role] = cfg.public_dump()
        if self.planner is not None:
            out.setdefault("planner", self.planner.public_dump())
        if self.adjudication is not None:
            out.setdefault("adjudication", self.adjudication.public_dump())
        if self.default is not None:
            out.setdefault("default", self.default.public_dump())
        return out

    def provider_ids(self) -> set[str]:
        ids = {cfg.provider for cfg in self.roles.values()}
        if self.default is not None:
            ids.add(self.default.provider)
        if self.planner is not None:
            ids.add(self.planner.provider)
        if self.adjudication is not None:
            ids.add(self.adjudication.provider)
        return ids


def _model_config_from_mapping(
    raw: dict[str, Any] | None,
    *,
    default_provider: str,
    default_model: str,
) -> ModelConfig:
    data = dict(raw or {})
    provider = str(data.pop("provider", default_provider) or default_provider)
    model = str(data.pop("model", default_model) or default_model)
    return ModelConfig(provider=provider, model=model, **data)


def routing_policy_from_config(config: LabConfig) -> RoutingPolicy:
    """Build a RoutingPolicy from LabConfig.

    If `routing.roles` is omitted, synthesize from top-level `provider` + `models`
    so pre-V2.4a YAML keeps working.
    """
    raw = dict(config.routing or {})
    version = str(raw.get("version") or "1")
    default_provider = config.provider
    default_model = next(iter((config.models or {}).values()), "deterministic")
    roles: dict[str, ModelConfig] = {}
    for role, model in (config.models or {}).items():
        roles[role] = ModelConfig(provider=default_provider, model=str(model))
    overlay = raw.get("roles") or {}
    if not isinstance(overlay, dict):
        raise ValueError("routing.roles must be a mapping")
    for role, cfg in overlay.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"routing.roles.{role} must be a mapping")
        fallback_model = (
            (config.models or {}).get(role) or default_model
        )
        roles[str(role)] = _model_config_from_mapping(
            cfg, default_provider=default_provider, default_model=str(fallback_model)
        )
    default_raw = raw.get("default")
    default_cfg: ModelConfig | None
    if default_raw is None:
        default_cfg = ModelConfig(provider=default_provider, model=default_model)
    elif isinstance(default_raw, dict):
        default_cfg = _model_config_from_mapping(
            default_raw, default_provider=default_provider, default_model=default_model
        )
    else:
        raise ValueError("routing.default must be a mapping")
    planner_raw = raw.get("planner")
    planner_cfg = None
    if isinstance(planner_raw, dict):
        planner_cfg = _model_config_from_mapping(
            planner_raw, default_provider=default_provider, default_model=default_model
        )
    adj_raw = raw.get("adjudication")
    adj_cfg = None
    if isinstance(adj_raw, dict):
        adj_cfg = _model_config_from_mapping(
            adj_raw, default_provider=default_provider, default_model=default_model
        )
    return RoutingPolicy(
        version=version,
        roles=roles,
        default=default_cfg,
        planner=planner_cfg,
        adjudication=adj_cfg,
    )


def independence_policy_from_config(config: LabConfig) -> IndependencePolicy:
    raw = dict(config.independence or {})
    return IndependencePolicy.model_validate(raw)


def apply_provider_override(config: LabConfig, provider: str) -> LabConfig:
    """CLI `--provider` remaps every role's provider; model ids stay as identifiers."""
    data = config.model_dump()
    data["provider"] = provider
    routing = dict(data.get("routing") or {})
    roles = dict(routing.get("roles") or {})
    for role, cfg in list(roles.items()):
        if isinstance(cfg, dict):
            roles[role] = {**cfg, "provider": provider}
    if roles:
        routing["roles"] = roles
    if isinstance(routing.get("default"), dict):
        routing["default"] = {**routing["default"], "provider": provider}
    if isinstance(routing.get("planner"), dict):
        routing["planner"] = {**routing["planner"], "provider": provider}
    if isinstance(routing.get("adjudication"), dict):
        routing["adjudication"] = {**routing["adjudication"], "provider": provider}
    data["routing"] = routing
    return LabConfig.model_validate(data)
