"""Hashing helpers for immutability and content addressing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return sha256_text(payload)


def claim_content_hash(statement: str, kind: str, evidence: str | None, math_check: dict | None) -> str:
    return sha256_json(
        {
            "statement": statement,
            "kind": kind,
            "evidence": evidence,
            "math_check": math_check,
        }
    )
