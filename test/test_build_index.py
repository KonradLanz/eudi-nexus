#!/usr/bin/env python3
"""
test/test_build_index.py

Unit tests for scripts/build-index.py

Run:
  pytest test/test_build_index.py -v
  pytest test/test_build_index.py -v -k "not embedding"  # skip LM Studio tests
  pytest test/test_build_index.py -v --run-embedding      # include LM Studio tests

The tests are fully offline by default — embedding tests are marked
@pytest.mark.embedding and skipped unless --run-embedding is passed.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

import pytest

# ── ensure scripts/ is importable ───────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util

def _load_build_index():
    spec = importlib.util.spec_from_file_location(
        "build_index", ROOT / "scripts" / "build-index.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

build_index = _load_build_index()


# ── pytest custom option ──────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--run-embedding", action="store_true", default=False,
        help="Run tests that require a live LM Studio embedding endpoint",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-embedding"):
        skip = pytest.mark.skip(reason="LM Studio not available; pass --run-embedding")
        for item in items:
            if "embedding" in item.keywords:
                item.add_marker(skip)


# ── Fixtures ─────────────────────────────────────────────────────────────────

MINIMAL_SEGMENTS_FILE = {
    "norm":    "EN 319 401",
    "version": "v2.2.1",
    "source_pdf": "/fake/path/en319401v020201p.pdf",
    "segmented_at": "2026-06-11T00:00:00Z",
    "segment_count": 5,
    "type_counts": {"NORM": 2, "SECTION": 1, "INFORM": 1, "HEADER": 1},
    "segments": [
        {
            "id": "en319401_p1_b1",
            "type": "HEADER",
            "page": 1,
            "anchor": "#page=1",
            "section": "",
            "section_title": "",
            "text": "ETSI EN 319 401",
            "normative_keywords": [],
            "profile": "etsi-spec",
        },
        {
            "id": "en319401_p5_b1",
            "type": "SECTION",
            "page": 5,
            "anchor": "#page=5",
            "section": "5",
            "section_title": "General requirements",
            "text": "5 General requirements",
            "normative_keywords": [],
            "profile": "etsi-spec",
        },
        {
            "id": "en319401_p5_b2",
            "type": "NORM",
            "page": 5,
            "anchor": "#page=5",
            "section": "5",
            "section_title": "General requirements",
            "text": "The TSP shall maintain audit logs of all relevant operations.",
            "normative_keywords": ["shall"],
            "profile": "etsi-spec",
        },
        {
            "id": "en319401_p6_b1",
            "type": "NORM",
            "page": 6,
            "anchor": "#page=6",
            "section": "5.1",
            "section_title": "Risk assessment",
            "text": "The TSP shall perform a risk assessment and document the results.",
            "normative_keywords": ["shall"],
            "profile": "etsi-spec",
        },
        {
            "id": "en319401_p7_b1",
            "type": "INFORM",
            "page": 7,
            "anchor": "#page=7",
            "section": "5.1",
            "section_title": "Risk assessment",
            "text": "NOTE: Risk assessment methodology should follow ISO 27005.",
            "normative_keywords": [],
            "profile": "etsi-spec",
        },
    ],
}


@pytest.fixture
def tmp_segments_dir(tmp_path: Path) -> Path:
    seg_dir = tmp_path / "corpus" / "specs" / "_segments"
    seg_dir.mkdir(parents=True)
    seg_file = seg_dir / "en319401v020201p.segments.json"
    seg_file.write_text(json.dumps(MINIMAL_SEGMENTS_FILE), encoding="utf-8")
    return seg_dir


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "corpus" / "test.db"


@pytest.fixture
def populated_db(tmp_segments_dir: Path, tmp_db: Path) -> sqlite3.Connection:
    """DB with schema + minimal segments inserted (no embeddings)."""
    con = build_index._open_db(tmp_db)
    build_index.create_schema(con, dims=4)
    build_index.index_segments_file(
        list(tmp_segments_dir.glob("*.segments.json"))[0],
        con,
        client=None,
        dry_run=False,
        verbose=False,
    )
    return con


# ── Unit: EmbeddingClient ────────────────────────────────────────────────

class TestEmbeddingClient:
    def test_ping_unreachable_returns_false(self):
        client = build_index.EmbeddingClient(base_url="http://localhost:19999")
        assert client.ping() is False

    def test_floats_to_blob_roundtrip(self):
        v = [0.1, 0.2, 0.3, 0.4]
        blob = build_index.floats_to_blob(v)
        unpacked = list(struct.unpack("4f", blob))
        assert len(unpacked) == 4
        for a, b in zip(v, unpacked):
            assert abs(a - b) < 1e-6

    @pytest.mark.embedding
    def test_embed_returns_correct_dimensions(self):
        client = build_index.EmbeddingClient()
        vectors = client.embed_batch(["hello world"])
        assert len(vectors) == 1
        assert len(vectors[0]) == build_index.DEFAULT_DIMS

    @pytest.mark.embedding
    def test_embed_batch_multiple(self):
        client = build_index.EmbeddingClient()
        texts = ["text one", "text two", "text three"]
        vectors = client.embed_batch(texts)
        assert len(vectors) == 3
        assert all(len(v) > 0 for v in vectors)


# ── Unit: load_segments_file ────────────────────────────────────────────

class TestLoadSegmentsFile:
    def test_parses_norm_and_version(self, tmp_path: Path):
        f = tmp_path / "test.segments.json"
        f.write_text(json.dumps(MINIMAL_SEGMENTS_FILE))
        norm, version, segs = build_index.load_segments_file(f)
        assert norm == "EN 319 401"
        assert version == "v2.2.1"
        assert len(segs) == 5

    def test_empty_segments_list(self, tmp_path: Path):
        data = {"norm": "EN 319 401", "version": "v1", "segments": []}
        f = tmp_path / "empty.segments.json"
        f.write_text(json.dumps(data))
        norm, version, segs = build_index.load_segments_file(f)
        assert segs == []


# ── Unit: DB schema ────────────────────────────────────────────────────────

class TestDBSchema:
    def test_create_schema_idempotent(self, tmp_db: Path):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=768)
        build_index.create_schema(con, dims=768)
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "segments" in tables
        assert "index_meta" in tables
        con.close()

    def test_drop_all_removes_tables(self, tmp_db: Path):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        build_index.drop_all(con)
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "segments" not in tables
        con.close()

    def test_fts_virtual_table_exists(self, tmp_db: Path):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        vtabs = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
        )}
        assert "segments_fts" in vtabs
        con.close()


# ── Integration: indexing ────────────────────────────────────────────────

class TestIndexing:
    def test_norm_segments_inserted(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments WHERE type='NORM'"
        ).fetchall()
        assert len(rows) == 2

    def test_inform_segments_inserted(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments WHERE type='INFORM'"
        ).fetchall()
        assert len(rows) == 1

    def test_section_segments_inserted(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments WHERE type='SECTION'"
        ).fetchall()
        assert len(rows) == 1

    def test_header_segments_skipped(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments WHERE type='HEADER'"
        ).fetchall()
        assert len(rows) == 0

    def test_anchor_stored_correctly(self, populated_db: sqlite3.Connection):
        row = populated_db.execute(
            "SELECT anchor FROM segments WHERE id='en319401_p5_b2'"
        ).fetchone()
        assert row is not None
        assert row["anchor"] == "#page=5"

    def test_normative_keywords_stored_as_json(self, populated_db: sqlite3.Connection):
        row = populated_db.execute(
            "SELECT normative_keywords FROM segments WHERE id='en319401_p5_b2'"
        ).fetchone()
        kw = json.loads(row["normative_keywords"])
        assert "shall" in kw

    def test_idempotent_no_duplicate_on_reindex(
        self, tmp_segments_dir: Path, tmp_db: Path
    ):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        seg_file = list(tmp_segments_dir.glob("*.segments.json"))[0]
        build_index.index_segments_file(seg_file, con, None, verbose=False)
        build_index.index_segments_file(seg_file, con, None, verbose=False)
        count = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        assert count == 4  # 2 NORM + 1 INFORM + 1 SECTION
        con.close()

    def test_already_indexed_flag(self, tmp_segments_dir: Path, tmp_db: Path):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        seg_file = list(tmp_segments_dir.glob("*.segments.json"))[0]
        build_index.index_segments_file(seg_file, con, None, verbose=False)
        assert build_index.already_indexed(con, seg_file.stem) is True
        con.close()

    def test_dry_run_does_not_write_to_db(
        self, tmp_segments_dir: Path, tmp_db: Path
    ):
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        seg_file = list(tmp_segments_dir.glob("*.segments.json"))[0]
        stats = build_index.index_segments_file(
            seg_file, con, None, dry_run=True, verbose=False
        )
        count = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        assert count == 0
        assert stats["inserted"] == 4
        con.close()


# ── Integration: FTS search ────────────────────────────────────────────

class TestFTSSearch:
    def test_fts_finds_shall_keyword(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments_fts WHERE segments_fts MATCH 'shall'"
        ).fetchall()
        ids = {r["id"] for r in rows}
        assert "en319401_p5_b2" in ids
        assert "en319401_p6_b1" in ids

    def test_fts_finds_audit_logs(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments_fts WHERE segments_fts MATCH '\"audit logs\"'"
        ).fetchall()
        assert len(rows) >= 1

    def test_fts_no_results_for_unknown_term(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            "SELECT id FROM segments_fts WHERE segments_fts MATCH 'xyznonexistent'"
        ).fetchall()
        assert len(rows) == 0

    def test_fts_rank_orders_by_relevance(self, populated_db: sqlite3.Connection):
        rows = populated_db.execute(
            """
            SELECT s.id, s.text, fts.rank
            FROM segments_fts fts
            JOIN segments s ON s.rowid = fts.rowid
            WHERE segments_fts MATCH 'risk assessment'
            ORDER BY fts.rank
            """
        ).fetchall()
        assert len(rows) >= 1
        assert "risk" in rows[0]["text"].lower()


# ── Integration: vector table ────────────────────────────────────────────

class TestVectorTable:
    def test_vector_table_empty_without_embeddings(
        self, populated_db: sqlite3.Connection
    ):
        count = populated_db.execute(
            "SELECT COUNT(*) FROM segments_vec"
        ).fetchone()[0]
        assert count == 0

    def test_manual_vector_insert_and_query(self, tmp_db: Path):
        """Smoke test: insert a known vector, retrieve with k=1 knn query."""
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=4)
        v = [1.0, 0.0, 0.0, 0.0]
        blob = build_index.floats_to_blob(v)
        con.execute(
            "INSERT INTO segments_vec(segment_id, embedding) VALUES (?, ?)",
            ("test_seg", blob),
        )
        con.commit()
        # sqlite-vec knn requires k = ? constraint
        rows = con.execute(
            """
            SELECT segment_id, distance
            FROM segments_vec
            WHERE embedding MATCH ?
              AND k = 1
            ORDER BY distance
            """,
            (blob,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "test_seg"
        assert rows[0][1] < 0.01
        con.close()


# ── Integration: LM Studio (skipped by default) ───────────────────────────

class TestWithLMStudio:
    @pytest.mark.embedding
    def test_full_index_with_real_embeddings(
        self, tmp_segments_dir: Path, tmp_db: Path
    ):
        client = build_index.EmbeddingClient()
        assert client.ping(), "LM Studio not reachable"
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=client.dims)
        seg_file = list(tmp_segments_dir.glob("*.segments.json"))[0]
        stats = build_index.index_segments_file(
            seg_file, con, client, dry_run=False, verbose=False
        )
        assert stats["embedded"] == 3   # 2 NORM + 1 INFORM
        assert stats["errors"] == 0
        vec_count = con.execute(
            "SELECT COUNT(*) FROM segments_vec"
        ).fetchone()[0]
        assert vec_count == 3
        con.close()
        client.close()

    @pytest.mark.embedding
    def test_cosine_search_finds_relevant_segment(
        self, tmp_segments_dir: Path, tmp_db: Path
    ):
        """End-to-end: embed query, knn search, top result contains 'shall'."""
        client = build_index.EmbeddingClient()
        assert client.ping(), "LM Studio not reachable"
        con = build_index._open_db(tmp_db)
        build_index.create_schema(con, dims=client.dims)
        seg_file = list(tmp_segments_dir.glob("*.segments.json"))[0]
        build_index.index_segments_file(
            seg_file, con, client, dry_run=False, verbose=False
        )
        q_vec = client.embed_batch(["TSP audit log requirements"])[0]
        blob  = build_index.floats_to_blob(q_vec)
        # k = ? is required by sqlite-vec for knn queries
        rows = con.execute(
            """
            SELECT v.segment_id, v.distance, s.text
            FROM segments_vec v
            JOIN segments s ON s.id = v.segment_id
            WHERE v.embedding MATCH ?
              AND k = 3
            ORDER BY v.distance
            """,
            (blob,),
        ).fetchall()
        assert len(rows) >= 1
        top = rows[0]["text"].lower()
        assert "shall" in top or "audit" in top
        con.close()
        client.close()
