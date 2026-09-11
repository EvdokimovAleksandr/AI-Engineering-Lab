"""Pipeline provenance is authoritative; LLM/tool-body text cannot raise trust or forge ids."""

from __future__ import annotations

from ai_lab.core.enums import SourceTrustTier


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


def lock_research_provenance(
    item: dict, tool_result: dict
) -> tuple[str | None, SourceTrustTier | None, dict, list[str]]:
    """Copy URI/hash/tier from retrieved sources. Never trust LLM or page text for identity."""
    sources = list(tool_result.get("sources") or [])
    evidence = list(tool_result.get("evidence") or [])
    by_uri = {str(s.get("uri")): s for s in sources if s.get("uri")}
    by_id = {str(s.get("source_id")): s for s in sources if s.get("source_id")}

    source = item.get("source")
    pipeline = None
    if source and str(source) in by_uri:
        pipeline = by_uri[str(source)]
    elif item.get("source_id") and str(item.get("source_id")) in by_id:
        pipeline = by_id[str(item.get("source_id"))]

    llm_tier = _parse_tier(item.get("source_trust"), source=source)
    conditions = dict(item.get("conditions") or {})
    refs: list[str] = list(item.get("refs") or [])

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
            if ev.get("source_id") == sid and ev.get("evidence_id"):
                refs.append(ev["evidence_id"])
        return source, source_trust, conditions, refs

    source_trust = llm_tier
    if source and str(source).startswith("mock://"):
        source_trust = SourceTrustTier.STUB
    conditions["retrieved"] = False
    conditions["data_not_instructions"] = True
    return source, source_trust, conditions, refs
