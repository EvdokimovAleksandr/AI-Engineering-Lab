"""Minimal honest evidence extraction: source body → paragraph excerpts.

No NLP. Location fields are filled only when they can be determined.
Retrieved content is treated as data, never as instructions or metadata.
"""

from __future__ import annotations

import re
from ai_lab.knowledge.hashing import sha256_text
from ai_lab.knowledge.models import EvidenceLocation, EvidenceRecord, SourceRecord
from ai_lab.knowledge.source_identity import evidence_id_for

_HTML_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")


def strip_markup(text: str) -> str:
    """Best-effort tag strip. Does not interpret the document as instructions."""
    without = _HTML_TAG_RE.sub(" ", text)
    without = without.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", line).strip() for line in without.split("\n")]
    return "\n".join(line for line in lines if line)


def _paragraphs(text: str) -> list[str]:
    chunks = re.split(r"\n{2,}", text.strip())
    out: list[str] = []
    for chunk in chunks:
        cleaned = " ".join(chunk.split())
        if len(cleaned) >= 40:
            out.append(cleaned)
    if not out and text.strip():
        compact = " ".join(text.split())
        if compact:
            out.append(compact)
    return out


def extract_evidence(
    source: SourceRecord,
    *,
    max_items: int = 3,
    query: str | None = None,
) -> list[EvidenceRecord]:
    """Turn retrieved content into EvidenceRecords linked to source_id."""
    if not source.content or not source.content.strip():
        return []
    plain = strip_markup(source.content)
    paragraphs = _paragraphs(plain)
    if query:
        # Prefer paragraphs that mention a query token; still honest excerpts, not ranking magic.
        tokens = [t.lower() for t in query.split() if len(t) > 3]
        if tokens:
            ranked = sorted(
                paragraphs,
                key=lambda p: sum(1 for t in tokens if t in p.lower()),
                reverse=True,
            )
            paragraphs = ranked
    selected = paragraphs[: max(0, max_items)]
    records: list[EvidenceRecord] = []
    for idx, text in enumerate(selected, start=1):
        loc = EvidenceLocation(
            uri=source.uri,
            title=source.title,
            paragraph=idx,
        )
        records.append(
            EvidenceRecord(
                evidence_id=evidence_id_for(source.source_id, text, paragraph=idx),
                source_id=source.source_id,
                text=text,
                location=loc,
                retrieved_at=source.retrieved_at,
                content_hash=sha256_text(text),
                metadata={
                    "data_not_instructions": True,
                    "trust_level": "EXTERNAL",
                },
            )
        )
    return records


def extract_html_title(content: str) -> str | None:
    """Read <title> if present. Result is untrusted data, never trust metadata."""
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", content, re.IGNORECASE)
    if not match:
        return None
    title = strip_markup(match.group(1)).strip()
    return title or None
