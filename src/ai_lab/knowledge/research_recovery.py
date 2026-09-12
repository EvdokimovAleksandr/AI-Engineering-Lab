"""Research query refinement and evidence-sufficiency evaluation.

Planner recovery (invalid DAG → StaticPlanner) is a different loop.
This module only changes search strategy inside a locked investigation scope.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ai_lab.core.enums import ResearchOutcome
from ai_lab.core.investigation import (
    InvestigationScope,
    ResearchQueryAttempt,
    ResearchRequirement,
    ResearchSufficiencyReport,
)
from ai_lab.knowledge.models import ResearchResult, SearchHit, SourceRecord
from ai_lab.knowledge.research_errors import (
    ResearchError,
    SearchProviderError,
    SearchTimeoutError,
    SourceFetchError,
)
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

ResearchFn = Callable[..., Awaitable[ResearchResult]]


@dataclass
class RecoveryOutcome:
    """Loop result: structured gate + last ResearchResult (may be empty / error)."""

    report: ResearchSufficiencyReport
    result: ResearchResult | None

_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "over",
}

# Domain synonyms used only to refine a query that stayed inside locked scope.
_SILK_REFINEMENTS = (
    "recombinant spider silk production",
    "spider silk fiber spinning",
    "industrial scale spider silk manufacturing",
    "recombinant silk protein processing",
)


def _new_qid() -> str:
    return f"q_{uuid4().hex[:12]}"


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", (text or "").lower())
        if t not in _STOP
    }


def query_preserves_scope(query: str, scope: InvestigationScope | None, parent: str) -> bool:
    """Block random topic drift: refined query must keep an anchor term from scope/parent."""
    q_toks = _tokens(query)
    anchors = _tokens(parent)
    if scope is not None:
        anchors |= _tokens(scope.objective)
        anchors |= {t.lower() for t in scope.key_terms}
    if not anchors:
        return True
    overlap = q_toks & anchors
    if overlap:
        # Domain lock: a silk investigation cannot drift to "spider population".
        domain_must = {"silk", "шёлк", "шелк"}
        if anchors & domain_must and not (q_toks & domain_must):
            logger.error("Refined query dropped domain term silk: %r", query)
            return False
        return True
    logger.error(
        "Refined query drifts from scope: %r vs anchors=%s", query, sorted(anchors)[:12]
    )
    return False


def classify_research_result(
    result: ResearchResult,
    *,
    key_terms: list[str] | None = None,
    provider_error: str | None = None,
) -> ResearchOutcome:
    """Distinguish provider failure, empty hits, filtered hits, and success."""
    if provider_error:
        return ResearchOutcome.RESEARCH_PROVIDER_ERROR
    raw_hits = int((result.metadata or {}).get("raw_hit_count") or 0)
    rejected = result.metadata.get("rejected_hits") if result.metadata else None
    rejected_n = len(rejected) if isinstance(rejected, list) else 0
    sources = list(result.sources or [])
    if raw_hits == 0 and not sources:
        return ResearchOutcome.RESEARCH_EMPTY
    retained = _relevant_sources(sources, key_terms or [], result.query)
    if sources and not retained:
        return ResearchOutcome.RESEARCH_FILTERED
    if raw_hits > 0 and not sources and rejected_n:
        return ResearchOutcome.RESEARCH_FILTERED
    if sources:
        return ResearchOutcome.RESEARCH_SUCCESS
    return ResearchOutcome.RESEARCH_EMPTY


def _relevant_sources(
    sources: list[SourceRecord], key_terms: list[str], query: str
) -> list[SourceRecord]:
    if not key_terms:
        return list(sources)
    needles = {t.lower() for t in key_terms} | _tokens(query)
    kept: list[SourceRecord] = []
    for src in sources:
        preview = ""
        if src.content:
            preview = src.content[:400]
        elif isinstance(src.metadata, dict):
            preview = str(src.metadata.get("content_preview") or "")[:400]
        blob = " ".join(x for x in (src.title, src.uri, preview, str(src.metadata)) if x).lower()
        if any(n in blob for n in needles if len(n) >= 4):
            kept.append(src)
    return kept


def evaluate_coverage(
    requirements: list[ResearchRequirement],
    *,
    sources: list[SourceRecord],
    findings_text: str = "",
) -> list[ResearchRequirement]:
    """Requirement-based coverage. source_count > 0 is not sufficient."""
    blob = " ".join(
        [
            findings_text,
            *[f"{s.title or ''} {s.uri} {(s.content or '')[:800]}" for s in sources],
        ]
    ).lower()
    out: list[ResearchRequirement] = []
    for req in requirements:
        terms = _tokens(req.topic)
        hits = sum(1 for t in terms if t in blob)
        if not terms:
            coverage = "PASS" if sources else "FAIL"
        elif hits >= max(1, len(terms) // 2):
            coverage = "PASS"
        elif hits >= 1:
            coverage = "PARTIAL"
        else:
            coverage = "FAIL"
        matched = [
            s.source_id
            for s in sources
            if any(
                t in ((s.title or "") + (s.content or "")[:400]).lower() for t in terms
            )
        ]
        out.append(req.model_copy(update={"coverage": coverage, "source_ids": matched}))
    return out


def initial_query(scope: InvestigationScope | None, fallback: str) -> str:
    if scope is None:
        return (fallback or "").strip()
    if scope.key_terms:
        return " ".join(scope.key_terms[:6]).strip()
    return (scope.objective or fallback or "").strip()


def refine_query(
    parent: str,
    *,
    attempt_index: int,
    outcome: ResearchOutcome,
    scope: InvestigationScope | None,
) -> tuple[str, str, str] | None:
    """Return (query, strategy, reason) or None if no safe refinement exists."""
    lower = parent.lower()
    silk = "silk" in lower or "пауч" in lower or (
        scope is not None and any("silk" in t.lower() for t in scope.key_terms)
    )

    if outcome == ResearchOutcome.RESEARCH_EMPTY and _is_over_narrow(parent):
        relaxed = _relax_constraints(parent)
        if relaxed != parent and query_preserves_scope(relaxed, scope, parent):
            return (
                relaxed,
                "relax_constraints",
                "initial query was overly specific (geo/year/throughput filters)",
            )

    if silk and attempt_index < len(_SILK_REFINEMENTS):
        nxt = _SILK_REFINEMENTS[attempt_index]
        if query_preserves_scope(nxt, scope, parent):
            return (
                nxt,
                "domain_specific_refinement",
                "initial query produced zero relevant technical sources",
            )

    if outcome in {ResearchOutcome.RESEARCH_EMPTY, ResearchOutcome.RESEARCH_FILTERED}:
        if scope and scope.key_terms:
            nxt = f"{parent} {scope.key_terms[min(attempt_index, len(scope.key_terms) - 1)]}"
            nxt = " ".join(nxt.split())
            if nxt != parent and query_preserves_scope(nxt, scope, parent):
                return (
                    nxt,
                    "terminology_expansion",
                    "adding locked scope terminology to recover relevant sources",
                )
        if scope and scope.objective and len(_tokens(parent)) <= 2:
            nxt = f"{parent} {scope.objective}"
            if query_preserves_scope(nxt, scope, parent):
                return (
                    nxt,
                    "increase_specificity",
                    "query was too broad relative to the locked objective",
                )

    return None


def _is_over_narrow(query: str) -> bool:
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", query))
    has_geo = bool(re.search(r"\b(russia|росси|usa|china|europe|европ)\b", query, re.I))
    has_qty = bool(re.search(r"\b\d+\s*(kg|т|ton|g/day|kg/day)\b", query, re.I))
    return sum([has_year, has_geo, has_qty]) >= 2


def _relax_constraints(query: str) -> str:
    out = re.sub(r"\b(19|20)\d{2}\b", " ", query)
    out = re.sub(
        r"\b(russia|росси[а-я]*|usa|china|europe|европ[а-я]*)\b", " ", out, flags=re.I
    )
    out = re.sub(r"\b\d+\s*(kg|т|ton|g/day|kg/day)(/day)?\b", " ", out, flags=re.I)
    return " ".join(out.split())


async def recover_research(
    *,
    research: ResearchFn,
    initial: str,
    scope: InvestigationScope | None = None,
    max_refinements: int = 2,
    extra_kwargs: dict[str, Any] | None = None,
    on_attempt: Callable[[ResearchQueryAttempt], None] | None = None,
) -> RecoveryOutcome:
    """Bounded recovery loop. Counts as successive research.query attempts."""
    query = (initial or "").strip()
    if not query:
        logger.error("Research recovery called with empty initial query")
        raise ValueError("research recovery requires a non-empty initial query")

    requirements = list(scope.evidence_requirements) if scope else []
    research_required = bool(requirements) or (
        scope is not None and scope.pipeline_hint == "research"
    )
    attempts: list[ResearchQueryAttempt] = []
    last_result: ResearchResult | None = None
    last_outcome = ResearchOutcome.RESEARCH_EMPTY
    parent_id: str | None = None
    parent_q = query
    provider_error: str | None = None
    kwargs = dict(extra_kwargs or {})

    for attempt_i in range(1 + max(0, max_refinements)):
        qid = _new_qid()
        strategy = "initial" if attempt_i == 0 else attempts[-1].strategy
        reason = "primary search" if attempt_i == 0 else attempts[-1].reason
        if attempt_i > 0:
            nxt = refine_query(
                parent_q,
                attempt_index=attempt_i - 1,
                outcome=last_outcome,
                scope=scope,
            )
            if nxt is None:
                break
            query, strategy, reason = nxt
        try:
            result = await research(query, **kwargs)
            err = None
            if result.outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR:
                err = str((result.metadata or {}).get("provider_error") or "provider error")
            outcome = classify_research_result(
                result,
                key_terms=list(scope.key_terms) if scope else [],
                provider_error=err,
            )
            result = result.model_copy(
                update={
                    "outcome": outcome,
                    "parent_query": parent_q if attempt_i else None,
                    "strategy": strategy,
                    "reason": reason,
                }
            )
        except (SearchTimeoutError, SearchProviderError, SourceFetchError, ResearchError) as exc:
            logger.error("Research provider error on query %r: %s", query, exc)
            provider_error = str(exc)
            outcome = ResearchOutcome.RESEARCH_PROVIDER_ERROR
            result = ResearchResult(
                query=query,
                sources=[],
                evidence=[],
                findings=[],
                outcome=outcome,
                parent_query=parent_q if attempt_i else None,
                strategy=strategy,
                reason=reason,
                metadata={"provider_error": str(exc), "raw_hit_count": 0},
            )
            err = str(exc)

        last_result = result
        last_outcome = result.outcome or outcome
        if last_outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR:
            provider_error = err or provider_error
        attempts.append(
            ResearchQueryAttempt(
                query_id=qid,
                query=query,
                parent_query_id=parent_id,
                parent_query=parent_q if attempt_i else None,
                strategy=strategy,
                reason=reason,
                outcome=last_outcome,
                sources_found=int(
                    (result.metadata or {}).get("raw_hit_count") or len(result.sources)
                ),
                sources_retained=len(result.sources),
                provider_error=(
                    provider_error
                    if last_outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR
                    else None
                ),
            )
        )
        if on_attempt is not None:
            on_attempt(attempts[-1])
        parent_id = qid
        parent_q = query

        if last_outcome == ResearchOutcome.RESEARCH_SUCCESS:
            break
        if last_outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR and attempt_i >= 1:
            break

    covered = evaluate_coverage(
        requirements,
        sources=list(last_result.sources) if last_result else [],
        findings_text=" ".join(
            f.claim.statement for f in (last_result.findings if last_result else [])
        ),
    )
    covered_n = sum(1 for r in covered if r.coverage == "PASS")
    partial_n = sum(1 for r in covered if r.coverage == "PARTIAL")
    gaps = [r.topic for r in covered if r.coverage != "PASS"]

    if last_outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR:
        final = ResearchOutcome.RESEARCH_PROVIDER_ERROR
    elif not research_required:
        final = last_outcome
    elif covered and covered_n == len(covered):
        final = ResearchOutcome.RESEARCH_SUCCESS
    elif covered and (covered_n + partial_n) > 0:
        final = ResearchOutcome.RESEARCH_PARTIAL
    else:
        final = (
            last_outcome
            if last_outcome != ResearchOutcome.RESEARCH_SUCCESS
            else ResearchOutcome.RESEARCH_EMPTY
        )

    return RecoveryOutcome(
        report=ResearchSufficiencyReport(
            outcome=final,
            attempts=attempts,
            coverage=covered,
            required_count=len(covered),
            covered_count=covered_n,
            evidence_gaps=gaps,
            refinement_count=max(0, len(attempts) - 1),
            research_required=research_required,
            provider_error=provider_error,
        ),
        result=last_result,
    )


def hits_from_result_metadata(result: ResearchResult) -> list[SearchHit]:
    raw = (result.metadata or {}).get("hits") or []
    out: list[SearchHit] = []
    for item in raw:
        if isinstance(item, SearchHit):
            out.append(item)
        elif isinstance(item, dict) and item.get("uri"):
            out.append(SearchHit.model_validate(item))
    return out
