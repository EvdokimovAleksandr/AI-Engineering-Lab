"""Execution mode labels for benchmark / UI reports (PR-07 stage 15).

Maps LabConfig.provider / RunManifest.model_provider → a stable MODE tag so
MOCK stub research is not confused with LIVE research failure.
"""

from __future__ import annotations

# Canonical report labels — keep short for CLI and UI.
MODE_MOCK = "MOCK"
MODE_LIVE_CURSOR = "LIVE_CURSOR"
MODE_REPLAY = "REPLAY"
MODE_UNKNOWN = "UNKNOWN"

_PROVIDER_TO_MODE: dict[str, str] = {
    "mock": MODE_MOCK,
    "cursor_sdk": MODE_LIVE_CURSOR,
    "replay": MODE_REPLAY,
}


def execution_mode_from_provider(provider: str | None) -> str:
    """Return MODE: MOCK | LIVE_CURSOR | REPLAY | UNKNOWN.

    Raises nothing — unknown provider → UNKNOWN (caller may fail loud elsewhere).
    """
    key = (provider or "").strip().lower()
    if not key or key == "unknown":
        return MODE_UNKNOWN
    if key in _PROVIDER_TO_MODE:
        return _PROVIDER_TO_MODE[key]
    # Fail-visible: unexpected provider string is not silently remapped to MOCK.
    return MODE_UNKNOWN


def format_mode_line(provider: str | None) -> str:
    """Single report line: MODE: MOCK (provider=mock)."""
    mode = execution_mode_from_provider(provider)
    return f"MODE: {mode} (provider={provider or 'unknown'})"
