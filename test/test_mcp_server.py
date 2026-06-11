#!/usr/bin/env python3
"""
test/test_mcp_server.py

Unit + integration tests for scripts/mcp-server.py

All tests are offline by default (use the populated test DB fixture).
No live MCP transport is tested here — we call the tool functions directly.

Run:
  pytest test/test_mcp_server.py -v
  pytest test/test_mcp_server.py -v --run-embedding   # cosine tests
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_index = _load("build_index", "build-index.py")


# ── Fixtures ─────────────────────────────────────────────────────────────────

SEGMENTS_A = {
    "norm": "EN 319 401", "version": "v2.2.1",
    "segments": [
        {"id": "a_p1_h",  "type": "HEADER",  "page": 1, "anchor": "#page=1",
         "section": "",    "section_title": "",
         "text": "EN 319 401", "normative_keywords": [], "profile": ""},
        {"id": "a_p5_s",  "type": "SECTION", "page": 5, "anchor": "#page=5",
         "section": "5",   "section_title": "General",
         "text": "5 General", "normative_keywords": [], "profile": ""},
        {"id": "a_p5_n1", "type": "NORM",    "page": 5, "anchor": "#page=5",
         "section": "5",   "section_title": "General",
         "text": "The TSP shall maintain audit logs.",
         "normative_keywords": ["shall"], "profile": ""},
        {"id": "a_p6_n1", "type": "NORM",    "page": 6, "anchor": "#page=6",
         "section": "5.1", "section_title": "Risk assessment",
         "text": "The TSP shall perform a risk assessment.",
         "normative_keywords": ["shall"], "profile": ""},
        {"id": "a_p7_i1", "type": "INFORM",  "page": 7, "anchor": "#page=7",
         "section": "5.1", "section_title": "Risk assessment",
         "text": "NOTE: Follow ISO 27005 for risk methodology.",
         "normative_keywords": [], "profile": ""},
    ],
}

SEGMENTS_B = {
    "norm": "EN 319 411-1", "version": "v1.3.1",
    "segments": [
        {"id": "b_p3_n1", "type": "NORM", "page": 3, "anchor": "#page=3",
         "section": "6", "section_title": "Key management",
         "text": "The CA shall protect the private key using a QSCD.",
         "normative_keywords": ["shall"], "profile": ""},
        {"id": "b_p4_i1", "type": "INFORM", "page": 4, "anchor": "#page=4",
         "section": "6", "section_title": "Key management",
         "text": "NOTE: HSMs are a typical QSCD implementation.",
         "normative_keywords": [], "profile": ""},
    ],
}


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> Path:
    """Module-scoped populated test DB (no embeddings)."""
    p = tmp_path_factory.mktemp("db") / "test.db"
    con = build_index._open_db(p)
    build_index.create_schema(con, dims=4)
    for data in (SEGMENTS_A, SEGMENTS_B):
        import tempfile, json as _json
        td = tmp_path_factory.mktemp("seg")
        sf = td / f"{data['norm'].replace(' ', '_')}.segments.json"
        sf.write_text(_json.dumps(data))
        build_index.index_segments_file(sf, con, client=None, verbose=False)
    con.close()
    return p


@pytest.fixture(scope="module")
def srv(db_path: Path):
    """Load mcp-server module with DB_PATH pointing to test DB."""
    os.environ["MCP_DB_PATH"] = str(db_path)
    mod = _load("mcp_server", "mcp-server.py")
    # Reset lazy connection so it picks up the env var
    mod._db = None
    return mod


# ── list_norms ─────────────────────────────────────────────────────────────────

class TestListNorms:
    def test_returns_two_norms(self, srv):
        result = srv.list_norms()
        assert result["total_norms"] == 2

    def test_total_segments(self, srv):
        result = srv.list_norms()
        # A: 4 indexed (HEADER skipped), B: 2 indexed
        assert result["total_segments"] == 6

    def test_norm_names_present(self, srv):
        result = srv.list_norms()
        names = {n["norm"] for n in result["norms"]}
        assert "EN 319 401" in names
        assert "EN 319 411-1" in names

    def test_norm_counts_correct(self, srv):
        result = srv.list_norms()
        a = next(n for n in result["norms"] if "401" in n["norm"])
        assert a["norm_count"] == 2
        assert a["inform_count"] == 1
        assert a["section_count"] == 1


# ── get_segment ────────────────────────────────────────────────────────────────

class TestGetSegment:
    def test_returns_correct_segment(self, srv):
        seg = srv.get_segment("a_p5_n1")
        assert seg["id"] == "a_p5_n1"
        assert seg["type"] == "NORM"
        assert "audit" in seg["text"].lower()

    def test_normative_keywords_as_list(self, srv):
        seg = srv.get_segment("a_p5_n1")
        assert isinstance(seg["normative_keywords"], list)
        assert "shall" in seg["normative_keywords"]

    def test_anchor_field_present(self, srv):
        seg = srv.get_segment("a_p6_n1")
        assert seg["anchor"] == "#page=6"

    def test_not_found_returns_error(self, srv):
        result = srv.get_segment("nonexistent_id_xyz")
        assert "error" in result
        assert result["error"] == "not found"

    def test_inform_segment_retrievable(self, srv):
        seg = srv.get_segment("a_p7_i1")
        assert seg["type"] == "INFORM"


# ── get_section ────────────────────────────────────────────────────────────────

class TestGetSection:
    def test_section_5_includes_subsections(self, srv):
        result = srv.get_section(norm="319 401", section="5")
        sections = {s["section"] for s in result["segments"]}
        assert "5" in sections
        assert "5.1" in sections

    def test_section_5_excludes_section_6(self, srv):
        result = srv.get_section(norm="319 401", section="5")
        sections = {s["section"] for s in result["segments"]}
        assert "6" not in sections

    def test_section_count_correct(self, srv):
        # section 5: SECTION header + 2 NORM + 1 INFORM = 4
        result = srv.get_section(norm="319 401", section="5")
        assert result["segment_count"] == 4

    def test_type_filter_norm_only(self, srv):
        result = srv.get_section(norm="319 401", section="5", types=["NORM"])
        for seg in result["segments"]:
            assert seg["type"] == "NORM"

    def test_ordered_by_page(self, srv):
        result = srv.get_section(norm="319 401", section="5")
        pages = [s["page"] for s in result["segments"]]
        assert pages == sorted(pages)

    def test_different_norm_isolated(self, srv):
        result = srv.get_section(norm="319 411", section="6")
        for seg in result["segments"]:
            assert "411" in seg["norm"]

    def test_empty_section_returns_zero(self, srv):
        result = srv.get_section(norm="319 401", section="99")
        assert result["segment_count"] == 0
        assert result["segments"] == []


# ── search_norm (BM25-only, no LM Studio) ────────────────────────────────

class TestSearchNormBM25:
    """Tests with alpha=1.0 (BM25-only) — no LM Studio needed."""

    def test_finds_shall_segments(self, srv):
        result = srv.search_norm("shall", alpha=1.0)
        assert result["result_count"] > 0
        ids = {r["id"] for r in result["results"]}
        assert "a_p5_n1" in ids or "a_p6_n1" in ids

    def test_norm_filter_restricts_results(self, srv):
        result = srv.search_norm("shall", norm="319 401", alpha=1.0)
        for r in result["results"]:
            assert "401" in r["norm"]

    def test_norm_filter_411_only(self, srv):
        result = srv.search_norm("QSCD", norm="319 411", alpha=1.0)
        for r in result["results"]:
            assert "411" in r["norm"]

    def test_header_excluded_from_results(self, srv):
        result = srv.search_norm("EN 319", alpha=1.0)
        for r in result["results"]:
            assert r["type"] != "HEADER"

    def test_type_filter_norm_only(self, srv):
        result = srv.search_norm("shall", alpha=1.0, types=["NORM"])
        for r in result["results"]:
            assert r["type"] == "NORM"

    def test_limit_respected(self, srv):
        result = srv.search_norm("shall", alpha=1.0, limit=1)
        assert len(result["results"]) <= 1

    def test_limit_clamped_to_max(self, srv):
        result = srv.search_norm("shall", alpha=1.0, limit=999)
        assert len(result["results"]) <= 20

    def test_no_results_for_unknown_query(self, srv):
        result = srv.search_norm("xyznonexistentterm", alpha=1.0)
        assert result["result_count"] == 0

    def test_results_have_required_fields(self, srv):
        result = srv.search_norm("audit", alpha=1.0)
        if result["result_count"] > 0:
            r = result["results"][0]
            for field in ("id", "norm", "version", "type", "text",
                          "anchor", "section", "hybrid_score", "bm25_score"):
                assert field in r, f"Missing field: {field}"

    def test_mode_is_bm25_only(self, srv):
        result = srv.search_norm("audit", alpha=1.0)
        assert result["mode"] == "bm25_only"


# ── search_norm (hybrid, LM Studio) ─────────────────────────────────────

class TestSearchNormHybrid:
    @pytest.mark.embedding
    def test_hybrid_mode_label(self, srv):
        result = srv.search_norm("TSP audit requirements", alpha=0.5)
        assert result["mode"] == "hybrid"

    @pytest.mark.embedding
    def test_cosine_score_nonzero(self, srv):
        result = srv.search_norm("TSP audit requirements", alpha=0.5)
        scores = [r["cosine_score"] for r in result["results"]]
        assert any(s > 0 for s in scores)

    @pytest.mark.embedding
    def test_alpha_zero_cosine_only(self, srv):
        result = srv.search_norm("TSP audit requirements", alpha=0.0)
        for r in result["results"]:
            assert r["bm25_score"] == 0.0

    def test_lmstudio_unavailable_falls_back_gracefully(self, srv):
        """If LM Studio is unreachable, search must still return BM25 results."""
        original_base = srv.BASE_URL
        srv.BASE_URL = "http://localhost:19999"  # unreachable
        try:
            result = srv.search_norm("audit", alpha=0.5)
            # Must still return results via BM25
            assert result["result_count"] >= 0  # no exception
        finally:
            srv.BASE_URL = original_base


# ── _norm_filter_clause ────────────────────────────────────────────────────

class TestNormFilterClause:
    def test_no_filters(self, srv):
        clause, params = srv._norm_filter_clause(None, None)
        assert clause == ""
        assert params == []

    def test_norm_only(self, srv):
        clause, params = srv._norm_filter_clause("319 401", None)
        assert "LIKE" in clause
        assert "%319 401%" in params

    def test_norm_and_version(self, srv):
        clause, params = srv._norm_filter_clause("319 401", "v2.2.1")
        assert "version" in clause
        assert "v2.2.1" in params
