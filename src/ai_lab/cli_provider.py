"""CLI: python -m ai_lab provider test — safe smoke check for an LLM backend.

Does not log secrets. Writes a sanitized JSON artifact under .runs/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from ai_lab.config_loader import load_config
from ai_lab.core.models import LabConfig, LLMMessage, LLMRequest
from ai_lab.llm.config import apply_provider_override, redact_secrets
from ai_lab.llm.errors import AuthenticationError, LLMProviderError, ProviderUnavailable
from ai_lab.llm.registry import create_provider
from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import repo_root_from_here

logger = get_logger(__name__)

_PROVIDER_CHOICES = ("mock", "cursor_sdk", "replay")


def build_provider_parser(sub: argparse._SubParsersAction) -> None:
    provider_p = sub.add_parser(
        "provider",
        help="LLM provider utilities (smoke test without a full lab run)",
    )
    provider_sub = provider_p.add_subparsers(dest="provider_command", required=True)

    test_p = provider_sub.add_parser(
        "test",
        help="Minimal structured completion against the selected provider",
    )
    test_p.add_argument(
        "--provider",
        choices=list(_PROVIDER_CHOICES),
        default="cursor_sdk",
        help="Provider id to smoke-test (default: cursor_sdk)",
    )
    test_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config (models / routing only; provider overridden)",
    )
    test_p.add_argument(
        "--model",
        default=None,
        help="Optional model id override for this smoke call",
    )


def _missing_key_instructions() -> str:
    return (
        "CURSOR_API_KEY is not set.\n"
        "1. Create a key: https://cursor.com/dashboard/integrations\n"
        "2. Copy .env.example -> .env and set CURSOR_API_KEY=...\n"
        "3. Install SDK: pip install -e \".[cursor]\"\n"
        "4. Verify key is present (do not print the value):\n"
        "     python -c \"import os; from dotenv import load_dotenv; "
        "load_dotenv(); print('set' if os.getenv('CURSOR_API_KEY') else 'missing')\"\n"
        "5. Confirm .env is not tracked: git check-ignore -v .env\n"
        "For offline work use: python -m ai_lab provider test --provider mock"
    )


def _key_present() -> bool:
    value = os.environ.get("CURSOR_API_KEY")
    return bool(value and value.strip())


async def _run_smoke(
    *,
    config: LabConfig,
    provider_id: str,
    model: str | None,
) -> dict:
    """Create one provider instance and ask for a tiny JSON object."""
    provider = create_provider(provider_id, config)
    model_id = model or config.models.get("chief_engineer") or "composer-2.5"
    request = LLMRequest(
        messages=[
            LLMMessage(
                role="user",
                content=(
                    "Provider smoke test. Reply with ONLY this JSON object:\n"
                    '{"ok": true, "smoke": "ai_lab_provider_test"}'
                ),
            )
        ],
        model=model_id,
        response_schema_name="ProviderSmoke",
        metadata={"agent_role": "chief_engineer", "provider_smoke": True},
    )
    t0 = time.perf_counter()
    response = await provider.complete(request)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    usage = response.usage or {}
    return {
        "ok": True,
        "provider": provider_id,
        "model": response.model or model_id,
        "run_id": response.run_id,
        "duration_ms": round(duration_ms, 2),
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "usage": usage if usage else None,
        "parsed": response.parsed,
        "content_preview": (response.content or "")[:240],
        "reasoning_only_cwd": getattr(provider, "reasoning_only", None),
    }


def run_provider_cli(args: argparse.Namespace) -> int:
    if args.provider_command != "test":
        print(f"Unknown provider command: {args.provider_command}", file=sys.stderr)
        return 2

    provider_id = args.provider
    # Diagnose missing credentials before constructing the provider (no stack traces).
    if provider_id == "cursor_sdk" and not _key_present():
        print(_missing_key_instructions(), file=sys.stderr)
        return 1

    try:
        config = load_config(args.config)
        config = apply_provider_override(config, provider_id)
    except Exception as exc:
        logger.error("Failed to load config for provider test: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(
            _run_smoke(config=config, provider_id=provider_id, model=args.model)
        )
    except AuthenticationError as exc:
        print(str(exc), file=sys.stderr)
        if provider_id == "cursor_sdk":
            print(_missing_key_instructions(), file=sys.stderr)
        return 1
    except ProviderUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except LLMProviderError as exc:
        logger.error("Provider smoke failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Unexpected — still avoid dumping secrets; message only.
        logger.error("Provider smoke unexpected failure: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    sanitized = {
        "ok": bool(result.get("ok")),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "run_id": result.get("run_id"),
        "duration_ms": result.get("duration_ms"),
        # Usage is non-secret observability; keep explicit allow-list (never api keys).
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "usage": result.get("usage"),
        "parsed": result.get("parsed"),
        "content_preview": result.get("content_preview"),
        "reasoning_only_cwd": result.get("reasoning_only_cwd"),
    }
    # Defense-in-depth: drop any accidental secret-shaped keys from nested dicts.
    if isinstance(sanitized.get("parsed"), dict):
        sanitized["parsed"] = redact_secrets(sanitized["parsed"])
    if isinstance(sanitized.get("usage"), dict):
        # Keep only numeric token counters — never opaque blobs.
        usage_clean = {}
        for key in (
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            if key in sanitized["usage"]:
                usage_clean[key] = sanitized["usage"][key]
        sanitized["usage"] = usage_clean or None

    root = repo_root_from_here()
    runs_dir = root / ".runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    artifact = runs_dir / f"provider_test_{uuid4().hex[:12]}.json"
    artifact.write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"ok={sanitized.get('ok')}")
    print(f"provider={sanitized.get('provider')}")
    print(f"model={sanitized.get('model')}")
    print(f"run_id={sanitized.get('run_id')}")
    print(f"duration_ms={sanitized.get('duration_ms')}")
    print(f"input_tokens={sanitized.get('input_tokens')}")
    print(f"output_tokens={sanitized.get('output_tokens')}")
    print(f"artifact={artifact}")
    return 0
