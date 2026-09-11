"""Allowlisted environment for sandbox workers. Host secrets must not be inherited."""

from __future__ import annotations

import os
from pathlib import Path

# Только то, без чего интерпретатор на Windows/POSIX не стартует.
# Не копируем весь os.environ — иначе утекут API keys / .env.
_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "USERPROFILE",  # не кладём в env; listed so tests can prove we skip it
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONIOENCODING",
    }
)

# Даже если ключ в allowlist, эти имена/префиксы никогда не прокидываем.
_SECRET_NAMES = frozenset(
    {
        "CURSOR_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "HF_TOKEN",
    }
)

_SECRET_SUBSTRINGS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "ACCESS_KEY")

# USERPROFILE/HOME специально не копируем: sandbox не должен наследовать user profile paths.
_NEVER_COPY = frozenset({"USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"})


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    if upper in _SECRET_NAMES:
        return True
    return any(part in upper for part in _SECRET_SUBSTRINGS)


def build_sandbox_env(workspace: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construct child env from scratch. extra keys must themselves pass the allowlist."""
    tmp = workspace / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": "",
        "PYTHONSAFEPATH": "1",
        "AI_LAB_SANDBOX": "1",
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "TMPDIR": str(tmp),
    }
    for key in _ENV_ALLOWLIST:
        if key in _NEVER_COPY:
            continue
        if is_secret_env_name(key):
            continue
        val = os.environ.get(key)
        if val:
            env[key] = val
    for key, val in (extra or {}).items():
        if key in _NEVER_COPY or is_secret_env_name(key) or key not in _ENV_ALLOWLIST:
            raise ValueError(f"sandbox extra_env key {key!r} is not allowlisted")
        env[key] = val
    return env


_DOCKER_CLI_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Docker Desktop on Windows читает config из профиля — только для CLI, не контейнера.
        "USERPROFILE",
        "HOME",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
)


def build_docker_cli_env() -> dict[str, str]:
    """Env для процесса `docker` CLI на хосте. Не прокидывается в контейнер через -e."""
    from ai_lab.sandbox.docker_discover import local_docker_host_allowed

    env: dict[str, str] = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for key in _DOCKER_CLI_ALLOWLIST:
        if is_secret_env_name(key):
            continue
        val = os.environ.get(key)
        if not val:
            continue
        if key == "DOCKER_HOST" and not local_docker_host_allowed(val):
            raise ValueError(f"remote Docker daemon is not supported: DOCKER_HOST={val!r}")
        env[key] = val
    return env


def build_docker_container_extra_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Доп. -e только из явного allowlist. Host os.environ не копируется."""
    out: dict[str, str] = {}
    for key, val in (extra or {}).items():
        if key in _NEVER_COPY or is_secret_env_name(key) or key not in _ENV_ALLOWLIST:
            raise ValueError(f"sandbox extra_env key {key!r} is not allowlisted")
        out[key] = val
    return out
