"""V2.8 research recovery: empty vs filtered vs provider error, bounded retries."""

from __future__ import annotations

import pytest

from ai_lab.core.enums import AdjudicationStatus, EvidenceKind, ResearchOutcome
from ai_lab.core.investigation import ResearchRequirement, ResearchSufficiencyReport
from ai_lab.knowledge.models import ResearchResult, SourceRecord
from ai_lab.knowledge.research_errors import SearchTimeoutError
from ai_lab.knowledge.research_recovery import (
    classify_research_result,
    evaluate_coverage,
    query_preserves_scope,
    recover_research,
    refine_query,
)
from ai_lab.orchestrator.adjudication import adjudicate
from ai_lab.orchestrator.scope import resolve_scope


def _src(uri: str, title: str, content: str) -> SourceRecord:
    return SourceRecord(
        source_id=uri,
        uri=uri,
        title=title,
        content=content,
        content_hash=f"hash-{uri}",
    )


def _silk_scope():
    return resolve_scope("Research industrial spider silk production at manufacturing scale.")


@pytest.mark.asyncio
async def test_empty_research_triggers_refinement() -> None:
    queries: list[str] = []

    async def research(query: str, **_kw: object) -> ResearchResult:
        queries.append(query)
        return ResearchResult(query=query, sources=[], metadata={"raw_hit_count": 0})

    out = await recover_research(
        research=research,
        initial="industrial spider silk",
        scope=_silk_scope(),
        max_refinements=2,
    )
    assert len(queries) >= 2
    assert out.report.refinement_count >= 1
    assert out.report.outcome != ResearchOutcome.RESEARCH_SUCCESS
    assert queries[0] == "industrial spider silk"
    assert queries[1] != queries[0]


@pytest.mark.asyncio
async def test_refined_query_can_recover_sources() -> None:
    async def research(query: str, **_kw: object) -> ResearchResult:
        if "spinning" in query.lower() or "recombinant" in query.lower():
            return ResearchResult(
                query=query,
                sources=[
                    _src(
                        "mock://silk",
                        "Recombinant spider silk fiber spinning",
                        "recombinant protein production fiber spinning scale-up "
                        "mechanical properties industrial bottlenecks economics",
                    )
                ],
                metadata={"raw_hit_count": 4},
            )
        return ResearchResult(query=query, sources=[], metadata={"raw_hit_count": 0})

    out = await recover_research(
        research=research,
        initial="industrial spider silk",
        scope=_silk_scope(),
        max_refinements=2,
    )
    assert out.report.refinement_count >= 1
    assert out.result is not None
    assert out.result.sources
    assert out.report.outcome in {
        ResearchOutcome.RESEARCH_SUCCESS,
        ResearchOutcome.RESEARCH_PARTIAL,
    }


@pytest.mark.asyncio
async def test_research_provider_error_is_not_treated_as_no_evidence() -> None:
    async def research(query: str, **_kw: object) -> ResearchResult:
        raise SearchTimeoutError("provider timeout")

    out = await recover_research(
        research=research,
        initial="industrial spider silk",
        scope=_silk_scope(),
        max_refinements=2,
    )
    assert out.report.outcome == ResearchOutcome.RESEARCH_PROVIDER_ERROR
    assert out.report.provider_error
    blob = " ".join(out.report.evidence_gaps).lower()
    assert "no evidence exists" not in blob
    reasons = adjudicate(
        check_report=None,
        verification=None,
        red_team=None,
        require_independent_review=False,
        require_red_team=False,
        verification_required=False,
        research_sufficiency=out.report,
        scope_status="SCOPE_RESOLVED",
    )
    assert reasons.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE
    assert any("PROVIDER_ERROR" in r for r in reasons.reasons)
    assert all("no evidence exists" not in r.lower() for r in reasons.reasons)


def test_filtered_results_are_distinguished_from_empty_provider() -> None:
    result = ResearchResult(
        query="spider silk",
        sources=[_src("https://example.com/weather", "Local weather", "rain and clouds")],
        metadata={"raw_hit_count": 20, "rejected_hits": [{"uri": "x"}] * 19},
    )
    outcome = classify_research_result(
        result, key_terms=["recombinant spider silk", "fiber spinning"]
    )
    assert outcome == ResearchOutcome.RESEARCH_FILTERED
    empty = classify_research_result(
        ResearchResult(query="q", sources=[], metadata={"raw_hit_count": 0})
    )
    assert empty == ResearchOutcome.RESEARCH_EMPTY


@pytest.mark.asyncio
async def test_research_retry_budget_is_bounded() -> None:
    n = {"calls": 0}

    async def research(query: str, **_kw: object) -> ResearchResult:
        n["calls"] += 1
        return ResearchResult(query=query, sources=[], metadata={"raw_hit_count": 0})

    out = await recover_research(
        research=research,
        initial="industrial spider silk",
        scope=_silk_scope(),
        max_refinements=2,
    )
    assert n["calls"] <= 3
    assert out.report.refinement_count <= 2


def test_query_refinement_preserves_scope() -> None:
    scope = _silk_scope()
    assert query_preserves_scope(
        "recombinant spider silk fiber spinning",
        scope,
        "industrial spider silk production",
    )
    assert not query_preserves_scope(
        "spider population decline",
        scope,
        "industrial spider silk production",
    )


def test_too_narrow_query_relaxes_constraints() -> None:
    nxt = refine_query(
        "industrial spider silk electrospinning throughput Russia 2023 100kg/day",
        attempt_index=0,
        outcome=ResearchOutcome.RESEARCH_EMPTY,
        scope=_silk_scope(),
    )
    assert nxt is not None
    query, strategy, _reason = nxt
    assert strategy == "relax_constraints"
    assert "2023" not in query
    assert "Russia" not in query
    assert "silk" in query.lower()


def test_too_broad_low_relevance_is_not_success() -> None:
    # Hits exist, but none match locked technical terms — not RESEARCH_SUCCESS.
    result = ResearchResult(
        query="nnnmmm unspecified dump",
        sources=[
            _src("https://example.com/fashion", "Retail week", "retail fashion week"),
            _src("https://example.com/zoo", "Visitor hours", "zoo visitors"),
        ],
        metadata={"raw_hit_count": 40},
    )
    outcome = classify_research_result(
        result, key_terms=["recombinant protein", "fiber spinning", "industrial production"]
    )
    assert outcome == ResearchOutcome.RESEARCH_FILTERED


def test_research_coverage_is_requirement_based() -> None:
    reqs = [
        ResearchRequirement(topic="manufacturing"),
        ResearchRequirement(topic="scaling"),
        ResearchRequirement(topic="economics"),
        ResearchRequirement(topic="material properties"),
    ]
    sources = [
        _src("a", "Manufacturing recombinant silk", "manufacturing material properties fiber"),
        _src("b", "Tensile properties", "material properties tensile strength"),
    ]
    covered = evaluate_coverage(reqs, sources=sources, findings_text="")
    by_topic = {r.topic: r.coverage for r in covered}
    assert by_topic["manufacturing"] == "PASS"
    assert by_topic["material properties"] == "PASS"
    assert by_topic["economics"] == "FAIL"
    assert by_topic["scaling"] == "FAIL"


def test_partial_coverage_produces_partial_support() -> None:
    report = ResearchSufficiencyReport(
        outcome=ResearchOutcome.RESEARCH_PARTIAL,
        required_count=4,
        covered_count=2,
        research_required=True,
        evidence_gaps=["economics", "scaling"],
        coverage=[
            ResearchRequirement(topic="manufacturing", coverage="PASS"),
            ResearchRequirement(topic="scaling", coverage="FAIL"),
            ResearchRequirement(topic="economics", coverage="FAIL"),
            ResearchRequirement(topic="material properties", coverage="PASS"),
        ],
    )
    adj = adjudicate(
        check_report=None,
        verification=None,
        red_team=None,
        require_independent_review=False,
        require_red_team=False,
        verification_required=False,
        research_sufficiency=report,
        scope_status="SCOPE_RESOLVED",
    )
    assert adj.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE
    assert any("PARTIAL" in r for r in adj.reasons)
    assert adj.status != AdjudicationStatus.PASS
