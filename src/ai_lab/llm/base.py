"""LLM request helpers and JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output; raise if none found."""
    text = text.strip()
    if not text:
        raise ValueError("Empty LLM response; cannot extract JSON")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    except json.JSONDecodeError:
        pass
    # Fenced block
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        data = json.loads(fence.group(1))
        if isinstance(data, dict):
            return data
    # First {...} span
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("No JSON object found in LLM response")
