"""LLM request helpers and JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# JSON string escapes per RFC 8259. Anything else (LaTeX \sigma, paths \Users) is invalid.
_JSON_SINGLE_ESCAPES = frozenset('"\\/bfnrt')
_HEX = frozenset("0123456789abcdefABCDEF")
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def repair_json_invalid_escapes(text: str) -> str:
    """Double backslashes that are not valid JSON escape sequences.

    Cursor/LLM output often contains LaTeX (``\\sigma``, ``\\pi``) or Windows
    paths as a single backslash. That is the same characters the model wrote —
    this only makes them parseable. Valid escapes (``\\n``, ``\\"``, ``\\\\``,
    ``\\uXXXX``) are left unchanged. Missing fields are not invented.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            # Trailing backslash cannot start a valid escape.
            out.append("\\\\")
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _JSON_SINGLE_ESCAPES:
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        if nxt == "u" and i + 6 <= n and all(c in _HEX for c in text[i + 2 : i + 6]):
            out.append(text[i : i + 6])
            i += 6
            continue
        out.append("\\\\")
        out.append(nxt)
        i += 2
    return "".join(out)


def _loads_object(blob: str) -> dict[str, Any]:
    data = json.loads(blob)
    if isinstance(data, dict):
        return data
    raise ValueError(f"Expected JSON object, got {type(data).__name__}")


def _try_parse_object(blob: str) -> dict[str, Any] | None:
    """Parse one candidate blob; repair invalid backslash escapes once if needed."""
    try:
        return _loads_object(blob)
    except json.JSONDecodeError as exc:
        repaired = repair_json_invalid_escapes(blob)
        if repaired == blob:
            return None
        try:
            parsed = _loads_object(repaired)
        except (json.JSONDecodeError, ValueError):
            return None
        logger.warning(
            "LLM JSON had invalid escapes (%s at line %s col %s); repaired backslashes and parsed",
            exc.msg,
            exc.lineno,
            exc.colno,
        )
        return parsed
    except ValueError:
        # Parsed JSON that is not an object (array/number) — do not "repair" it.
        raise


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output; raise if none found."""
    text = text.strip()
    if not text:
        raise ValueError("Empty LLM response; cannot extract JSON")

    candidates: list[str] = [text]
    fence = _FENCE.search(text)
    if fence:
        candidates.append(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        span = text[start : end + 1]
        if span not in candidates:
            candidates.append(span)

    last_decode: json.JSONDecodeError | None = None
    for blob in candidates:
        try:
            parsed = _try_parse_object(blob)
        except ValueError:
            raise
        if parsed is not None:
            return parsed
        try:
            json.loads(blob)
        except json.JSONDecodeError as exc:
            last_decode = exc

    if last_decode is not None:
        pos = last_decode.pos if last_decode.pos is not None else 0
        lo = max(0, pos - 80)
        hi = min(len(text), pos + 80)
        snippet = text[lo:hi]
        raise ValueError(
            f"{last_decode.msg}: line {last_decode.lineno} column {last_decode.colno} "
            f"(char {last_decode.pos}); snippet={snippet!r}"
        ) from last_decode
    raise ValueError("No JSON object found in LLM response")
