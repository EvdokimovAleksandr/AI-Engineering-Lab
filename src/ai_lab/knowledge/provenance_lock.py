"""Pipeline provenance is authoritative; LLM/tool-body text cannot raise trust or forge ids."""

from __future__ import annotations

from typing import Any

from ai_lab.core.enums import SourceTrustTier
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


_TIER_RANK = {
    SourceTrustTier.STUB: 0,
    SourceTrustTier.SECONDARY: 1,
    SourceTrustTier.PRIMARY: 2,
}


def _parse_tier(raw, *, source: str | None) -> SourceTrustTier | None:
    if raw:
        try:
            return SourceTrustTier(str(raw))
        except ValueError:
            return SourceTrustTier.STUB if source and str(source).startswith("mock://") else SourceTrustTier.SECONDARY
    if source and str(source).startswith("mock://"):
        return SourceTrustTier.STUB
    return None


def conditions_as_dict(raw: Any) -> dict[str, Any]:
    """Claim.conditions is a dict. Live LLMs often emit a list or a string instead.

    ``dict(["F"])`` raises a cryptic TypeError and used to abort the whole run.
    A list/string is stored under ``notes`` (shape fix, not a trust/role remap).
    Other types fail loudly.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        logger.error("Research conditions was a string (expected object); storing under conditions.notes")
        return {"notes": [text]}
    if isinstance(raw, list):
        notes: list[str] = []
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(
                    item.get("statement")
                    or item.get("text")
                    or item.get("condition")
                    or item.get("description")
                    or ""
                ).strip()
            else:
                text = str(item).strip()
            if text:
                notes.append(text)
        logger.error(
            "Research conditions was a list (expected object); storing %s note(s) under conditions.notes",
            len(notes),
        )
        return {"notes": notes} if notes else {}
    logger.error("Research conditions has unexpected type %s", type(raw).__name__)
    raise ValueError(
        f"Research finding conditions must be an object, got {type(raw).__name__}"
    )


def lock_research_provenance(
    item: dict, tool_result: dict
) -> tuple[str | None, SourceTrustTier | None, dict, list[str]]:
    """Copy URI/hash/tier from retrieved sources. Never trust LLM or page text for identity."""
    sources = list(tool_result.get("sources") or [])
    evidence = list(tool_result.get("evidence") or [])
    by_uri = {str(s.get("uri")): s for s in sources if isinstance(s, dict) and s.get("uri")}
    by_id = {str(s.get("source_id")): s for s in sources if isinstance(s, dict) and s.get("source_id")}

    source = item.get("source")
    pipeline = None
    if source and str(source) in by_uri:
        pipeline = by_uri[str(source)]
    elif item.get("source_id") and str(item.get("source_id")) in by_id:
        pipeline = by_id[str(item.get("source_id"))]

    llm_tier = _parse_tier(item.get("source_trust"), source=source)
    conditions = conditions_as_dict(item.get("conditions"))
    refs: list[str] = list(item.get("refs") or []) if isinstance(item.get("refs"), list) else []

    if pipeline:
        source = pipeline.get("uri") or source
        pipeline_tier = _parse_tier(pipeline.get("trust_tier"), source=source) or SourceTrustTier.SECONDARY
        if llm_tier is None or _TIER_RANK[llm_tier] > _TIER_RANK[pipeline_tier]:
            source_trust = pipeline_tier
        else:
            source_trust = llm_tier
        conditions["source_id"] = pipeline.get("source_id")
        conditions["content_hash"] = pipeline.get("content_hash")
        conditions["retrieved_at"] = pipeline.get("retrieved_at")
        conditions["source_type"] = pipeline.get("source_type")
        conditions["data_not_instructions"] = True
        sid = pipeline.get("source_id")
        if sid and sid not in refs:
            refs.append(sid)
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            if ev.get("source_id") == sid and ev.get("evidence_id"):
                refs.append(ev["evidence_id"])
        return source, source_trust, conditions, refs

    source_trust = llm_tier
    if source and str(source).startswith("mock://"):
        source_trust = SourceTrustTier.STUB
    conditions["retrieved"] = False
    conditions["data_not_instructions"] = True
    return source, source_trust, conditions, refs
