"""LLM JSON extraction must parse Cursor/LaTeX backslashes without inventing fields."""

from __future__ import annotations

import json

import pytest

from ai_lab.llm.base import extract_json_object, repair_json_invalid_escapes


def test_extract_valid_json_unchanged() -> None:
    payload = {"code": "import math\nprint(1)\n", "n": 1, "q": 'say "hi"'}
    text = json.dumps(payload)
    assert extract_json_object(text) == payload


def test_extract_repairs_latex_sigma_pi_escapes() -> None:
    # Live Cursor simulation output: raw \sigma / \pi inside a JSON string.
    raw = '{"expression": "\\sigma = 4F/(\\pi * d**2)", "ok": true}'
    with pytest.raises(json.JSONDecodeError, match="Invalid \\\\escape"):
        json.loads(raw)
    parsed = extract_json_object(raw)
    assert parsed["ok"] is True
    assert parsed["expression"] == r"\sigma = 4F/(\pi * d**2)"


def test_extract_repairs_windows_path_escape() -> None:
    raw = r'{"path": "C:\Users\evd19\work"}'
    parsed = extract_json_object(raw)
    assert "Users" in parsed["path"]
    assert parsed["path"].startswith("C:")


def test_repair_leaves_valid_unicode_and_quotes() -> None:
    original = '{"s": "line\\nnext", "q": "say \\"hi\\"", "u": "\\u03c0"}'
    assert repair_json_invalid_escapes(original) == original
    parsed = extract_json_object(original)
    assert parsed["s"] == "line\nnext"
    assert parsed["q"] == 'say "hi"'
    assert parsed["u"] == "π"


def test_extract_fenced_invalid_escape() -> None:
    text = 'note\n```json\n{"k": "\\sigma"}\n```\n'
    parsed = extract_json_object(text)
    assert parsed == {"k": r"\sigma"}


def test_extract_empty_raises() -> None:
    with pytest.raises(ValueError, match="Empty"):
        extract_json_object("   ")


def test_extract_non_object_raises() -> None:
    with pytest.raises(ValueError, match="Expected JSON object"):
        extract_json_object("[1, 2]")


def test_extract_garbage_still_raises() -> None:
    with pytest.raises(ValueError, match="Expecting value|No JSON object found"):
        extract_json_object("not-json-at-all")
