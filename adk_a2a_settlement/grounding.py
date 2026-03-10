"""Web grounding for deliverable pre-verification.

Uses ADK's Google Search Grounding tool to verify deliverable content
against web sources before submission. Transforms Gemini's grounding
metadata (groundingChunks, groundingSupports) into settlement provenance
that the exchange and AI Mediator can evaluate.

Usage:
    from adk_a2a_settlement.grounding import ground_deliverable, build_grounded_provenance

    result = await ground_deliverable("The GDP of France in 2025 was...")
    provenance = build_grounded_provenance(result)
    exchange.deliver(escrow_id=eid, content=content, provenance=provenance)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("adk_a2a_settlement.grounding")

DEFAULT_GROUNDING_MODEL = "gemini-2.5-flash"

GROUNDING_VERIFICATION_PROMPT = (
    "Verify the following claims by grounding them against authoritative web "
    "sources. Reproduce the key factual claims with citations.\n\n{content}"
)


@dataclass
class GroundingResult:
    """Structured result from a Google Search grounding call."""

    grounded_text: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    supports: list[dict[str, Any]] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    coverage: float = 0.0


async def ground_deliverable(
    content: str,
    *,
    model: str = DEFAULT_GROUNDING_MODEL,
    prompt_template: str | None = None,
) -> GroundingResult:
    """Ground deliverable content against web sources via Gemini.

    Sends the content through a Gemini model with the ``google_search``
    tool enabled, then extracts the ``groundingMetadata`` from the
    response.

    Args:
        content: The deliverable text to ground.
        model: Gemini model to use for grounding.
        prompt_template: Optional prompt template with a ``{content}``
            placeholder.  Defaults to a verification-oriented prompt.

    Returns:
        A :class:`GroundingResult` with chunks, supports, queries, and
        a coverage score indicating what fraction of the grounded text
        is backed by source segments.
    """
    from google import genai
    from google.adk.tools import google_search

    template = prompt_template or GROUNDING_VERIFICATION_PROMPT
    prompt = template.format(content=content)

    client = genai.Client()
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            tools=[google_search],
            temperature=0.0,
        ),
    )

    return _extract_grounding(response)


def _extract_grounding(response: Any) -> GroundingResult:
    """Parse Gemini response into a GroundingResult."""
    grounded_text = ""
    if response.candidates:
        candidate = response.candidates[0]
        if candidate.content and candidate.content.parts:
            grounded_text = "".join(
                part.text
                for part in candidate.content.parts
                if hasattr(part, "text") and part.text
            )

    metadata = None
    if response.candidates:
        metadata = getattr(response.candidates[0], "grounding_metadata", None)

    if not metadata:
        logger.warning(
            "No grounding metadata in response — content may not be groundable"
        )
        return GroundingResult(grounded_text=grounded_text)

    chunks: list[dict[str, Any]] = []
    for chunk in getattr(metadata, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if web:
            chunks.append(
                {
                    "uri": getattr(web, "uri", ""),
                    "title": getattr(web, "title", None),
                }
            )

    supports: list[dict[str, Any]] = []
    for support in getattr(metadata, "grounding_supports", None) or []:
        segment = getattr(support, "segment", None)
        if segment:
            supports.append(
                {
                    "segment": {
                        "text": getattr(segment, "text", ""),
                        "start_index": getattr(segment, "start_index", 0),
                        "end_index": getattr(segment, "end_index", 0),
                    },
                    "chunk_indices": list(
                        getattr(support, "grounding_chunk_indices", None) or []
                    ),
                }
            )

    search_queries: list[str] = list(
        getattr(metadata, "web_search_queries", None) or []
    )

    coverage = _compute_coverage(grounded_text, supports)

    return GroundingResult(
        grounded_text=grounded_text,
        chunks=chunks,
        supports=supports,
        search_queries=search_queries,
        coverage=coverage,
    )


def _compute_coverage(text: str, supports: list[dict[str, Any]]) -> float:
    """Compute what fraction of the text is covered by grounding supports."""
    if not text or not supports:
        return 0.0

    text_len = len(text)
    covered = bytearray(text_len)

    for support in supports:
        seg = support.get("segment", {})
        start = seg.get("start_index", 0)
        end = seg.get("end_index", 0)
        start = max(0, min(start, text_len))
        end = max(start, min(end, text_len))
        for i in range(start, end):
            covered[i] = 1

    return sum(covered) / text_len


def build_grounded_provenance(result: GroundingResult) -> dict[str, Any]:
    """Transform a GroundingResult into a settlement provenance dict.

    The returned dict is ready to pass as the ``provenance`` argument to
    ``exchange.deliver()`` or ``provider.deliver()``.
    """
    now = datetime.now(timezone.utc).isoformat()

    source_refs = [
        {
            "uri": chunk["uri"],
            "method": "google_search_grounding",
            "timestamp": now,
            "content_hash": None,
        }
        for chunk in result.chunks
        if chunk.get("uri")
    ]

    grounding_metadata = {
        "chunks": result.chunks,
        "supports": result.supports,
        "search_queries": result.search_queries,
        "coverage": round(result.coverage, 4),
    }

    return {
        "source_type": "web",
        "source_refs": source_refs,
        "attestation_level": "verifiable",
        "signature": None,
        "grounding_metadata": grounding_metadata,
    }
