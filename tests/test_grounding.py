"""Tests for the grounding module — GroundingResult, build_grounded_provenance, coverage."""

from __future__ import annotations

from adk_a2a_settlement.grounding import (
    GroundingResult,
    _compute_coverage,
    build_grounded_provenance,
)


class TestComputeCoverage:
    def test_empty_text_returns_zero(self):
        assert _compute_coverage("", []) == 0.0

    def test_empty_supports_returns_zero(self):
        assert _compute_coverage("hello world", []) == 0.0

    def test_full_coverage(self):
        text = "hello world"
        supports = [
            {"segment": {"start_index": 0, "end_index": len(text), "text": text}}
        ]
        assert _compute_coverage(text, supports) == 1.0

    def test_partial_coverage(self):
        text = "hello world"  # 11 chars
        supports = [{"segment": {"start_index": 0, "end_index": 5, "text": "hello"}}]
        coverage = _compute_coverage(text, supports)
        assert abs(coverage - 5 / 11) < 0.01

    def test_overlapping_segments(self):
        text = "hello world"  # 11 chars
        supports = [
            {"segment": {"start_index": 0, "end_index": 7, "text": "hello w"}},
            {"segment": {"start_index": 5, "end_index": 11, "text": " world"}},
        ]
        coverage = _compute_coverage(text, supports)
        assert coverage == 1.0

    def test_out_of_bounds_clamped(self):
        text = "hi"
        supports = [{"segment": {"start_index": -5, "end_index": 100, "text": "hi"}}]
        coverage = _compute_coverage(text, supports)
        assert coverage == 1.0


class TestBuildGroundedProvenance:
    def test_basic_provenance_structure(self):
        result = GroundingResult(
            grounded_text="France GDP is $3T.",
            chunks=[
                {"uri": "https://worldbank.org/gdp", "title": "World Bank"},
                {"uri": "https://imf.org/data", "title": "IMF Data"},
            ],
            supports=[
                {
                    "segment": {
                        "text": "France GDP is $3T.",
                        "start_index": 0,
                        "end_index": 18,
                    },
                    "chunk_indices": [0, 1],
                }
            ],
            search_queries=["France GDP 2025"],
            coverage=0.85,
        )

        provenance = build_grounded_provenance(result)

        assert provenance["source_type"] == "web"
        assert provenance["attestation_level"] == "verifiable"
        assert len(provenance["source_refs"]) == 2
        assert provenance["source_refs"][0]["uri"] == "https://worldbank.org/gdp"
        assert provenance["source_refs"][0]["method"] == "google_search_grounding"
        assert provenance["grounding_metadata"] is not None
        assert provenance["grounding_metadata"]["coverage"] == 0.85
        assert len(provenance["grounding_metadata"]["chunks"]) == 2
        assert len(provenance["grounding_metadata"]["supports"]) == 1
        assert provenance["grounding_metadata"]["search_queries"] == ["France GDP 2025"]

    def test_empty_result_produces_minimal_provenance(self):
        result = GroundingResult(grounded_text="nothing grounded")
        provenance = build_grounded_provenance(result)

        assert provenance["source_type"] == "web"
        assert provenance["source_refs"] == []
        assert provenance["grounding_metadata"]["chunks"] == []
        assert provenance["grounding_metadata"]["supports"] == []
        assert provenance["grounding_metadata"]["coverage"] == 0.0

    def test_chunks_without_uri_skipped_in_source_refs(self):
        result = GroundingResult(
            grounded_text="test",
            chunks=[
                {"uri": "https://example.com", "title": "Ex"},
                {"uri": "", "title": "Empty"},
            ],
        )
        provenance = build_grounded_provenance(result)
        assert len(provenance["source_refs"]) == 1
        assert provenance["source_refs"][0]["uri"] == "https://example.com"


class TestGroundingResult:
    def test_defaults(self):
        r = GroundingResult(grounded_text="test")
        assert r.chunks == []
        assert r.supports == []
        assert r.search_queries == []
        assert r.coverage == 0.0
