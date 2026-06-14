#!/usr/bin/env python3
"""
build-index.py  —  Segment corpus → SQLite FTS5 + sqlite-vec index

Reads all corpus/specs/_segments/*.segments.json (produced by pdf-segment.py)
and builds a local SQLite database with:
  - FTS5 virtual table  → BM25 keyword search (built-in)
  - sqlite-vec table    → cosine similarity via float32 vectors
  - Metadata columns   → norm, version, type, section, anchor, keywords

Embeddings are fetched from LM Studio (LMSTUDIO_BASE_URL/v1/embeddings).
Only NORM and INFORM segments are embedded; SECTION is FTS-only.
HEADER / FOOTER / TOC / OTHER are skipped entirely.

Usage:
  python3 scripts/build-index.py              # idempotent (skip existing)
  python3 scripts/build-index.py --rebuild    # drop & recreate db
  python3 scripts/build-index.py --stats      # print index stats and exit
  python3 scripts/build-index.py --dry-run    # parse + embed, no db write
  python3 scripts/build-index.py --check-merge   # simulate merge quality gate, exit
  python3 scripts/build-index.py --check-index   # verify every doc landed in DB, exit
  python3 scripts/build-index.py --model nomic-embed-text-v1.5

Environment (from .env):
  LMSTUDIO_BASE_URL     http://localhost:1234
  EMBEDDING_MODEL       nomic-embed-text-v1.5
  EMBEDDING_DIMENSIONS  768
  MCP_DB_PATH           corpus/eudi-nexus.db

npm scripts (add to package.json):
  "index":         "python3 scripts/build-index.py",
  "index:rebuild": "python3 scripts/build-index.py --rebuild",
  "index:stats":   "python3 scripts/build-index.py --stats"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import struct
import sys
import time
from pathlib import Path
from typing import Iterator

try:
    import httpx
except ImportError:
    sys.exit("[ERROR] httpx not installed — run: pip install httpx")

try:
    import sqlite_vec
except ImportError:
    sys.exit("[ERROR] sqlite-vec not installed — run: pip install sqlite-vec")


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (no python-dotenv dependency)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

DEFAULT_BASE_URL   = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234")
DEFAULT_MODEL      = os.environ.get("EMBEDDING_MODEL",   "nomic-embed-text-v1.5")
DEFAULT_DIMS       = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))
DEFAULT_DB_PATH    = os.environ.get("MCP_DB_PATH", "corpus/eudi-nexus.db")
SEGMENTS_DIR       = Path("corpus/specs/_segments")

# Segment types to embed (full vector search)
EMBED_TYPES    = {"NORM", "INFORM"}
# Segment types to index FTS-only (no vector)
FTS_ONLY_TYPES = {"SECTION"}
# Segment types to skip entirely
SKIP_TYPES     = {"HEADER", "FOOTER", "TOC", "OTHER"}

# Batch size for embedding API calls
EMBED_BATCH = 16

# Segment merger thresholds
# MERGE_MIN_CHARS : flush threshold — only flush when buf is at least this long
#                   AND a sentence boundary is present (. ; :)
# MERGE_MAX_CHARS : hard cap — never produce a segment longer than this
#                   (avoids embedding model truncation at ~512 tokens ≈ 2000 chars)
# NOTE: _can_merge() must check against MERGE_MAX_CHARS, not MERGE_MIN_CHARS.
#       Using MERGE_MIN_CHARS as the merge-stop caused 64% of segments to be
#       hard-cut at MERGE_MAX_CHARS without a sentence boundary (median ~93 ch).
MERGE_MIN_CHARS = 200   # min length before we allow a sentence-end flush
MERGE_MAX_CHARS = 1800  # hard cap (raised from 1200 — nomic supports ~512 tok)

# Pipeline gate: if post-merge median stays below this, build-index exits 1
MERGE_TARGET_MEDIAN = 150

# check-index: minimum fraction of NORM/INFORM segments that must have embeddings
# (only enforced when the DB actually contains any embeddings at all)
EMBED_MIN_COVERAGE = 0.95   # 95 %


# ──────────────────────────────────────────────────────────────────────────────
# Segment merger
# ──────────────────────────────────────────────────────────────────────────────

def _merge_short_segments(segments: list[dict]) -> list[dict]:
    """
    Merge consecutive NORM/INFORM fragments within the same section
    into coherent requirement sentences.

    Rules:
    - Only merges segments of the same type within the same section.
    - section='' (anonymous/preamble zone) merges freely with other '' segments.
    - Numbered sections (e.g. '5.1') only merge within the same clause.
    - Any section change ('' → '5.1', '5.1' → '5.2') is a hard flush boundary.
    - Accumulates text until MERGE_MAX_CHARS would be exceeded.
    - Flushes eagerly once buf >= MERGE_MIN_CHARS AND a sentence ends (. ; :).
    - Hard-flushes when the next segment would push buf over MERGE_MAX_CHARS.
    - Non-NORM/INFORM segments (SECTION etc.) act as hard flush boundaries.
    - The merged segment inherits the ID, page, and anchor of the FIRST fragment.
    - normative_keywords is the union of all merged fragments.
    """
    merged: list[dict] = []
    buf: dict | None = None

    def _flush(b: dict) -> dict:
        """Normalise buffer before appending."""
        b["text"] = b["text"].strip()
        kw = b.get("normative_keywords")
        if isinstance(kw, list):
            b["normative_keywords"] = list(dict.fromkeys(kw))  # dedup, preserve order
        return b

    def _sentence_end(text: str) -> bool:
        t = text.rstrip()
        return t.endswith((".", ";", ":"))

    def _can_merge(buf: dict, seg: dict) -> bool:
        # Section equality is the key gate:
        #   '' == ''     → same anonymous zone, allowed
        #   '5.1'=='5.1' → same clause, allowed
        #   anything else → different section, hard boundary
        return (
            buf["type"] == seg.get("type")
            and buf.get("section") == seg.get("section")
            and len(buf["text"]) + 1 + len(seg.get("text", "")) <= MERGE_MAX_CHARS
        )

    for seg in segments:
        seg_type = seg.get("type", "OTHER")

        # Non-mergeable types flush the buffer immediately
        if seg_type not in EMBED_TYPES:
            if buf is not None:
                merged.append(_flush(buf))
                buf = None
            merged.append(seg)
            continue

        if buf is None:
            buf = dict(seg)
            buf["normative_keywords"] = list(seg.get("normative_keywords") or [])
        elif _can_merge(buf, seg):
            # Append text with a space separator
            buf["text"] = buf["text"].rstrip() + " " + seg["text"].strip()
            # Union of keywords
            existing = set(buf["normative_keywords"])
            for kw in (seg.get("normative_keywords") or []):
                if kw not in existing:
                    buf["normative_keywords"].append(kw)
                    existing.add(kw)
        else:
            # Section changed or would exceed MERGE_MAX_CHARS — flush and start new
            merged.append(_flush(buf))
            buf = dict(seg)
            buf["normative_keywords"] = list(seg.get("normative_keywords") or [])

        # Eager flush: long enough AND sentence boundary present
        if buf and len(buf["text"]) >= MERGE_MIN_CHARS and _sentence_end(buf["text"]):
            merged.append(_flush(buf))
            buf = None

    if buf is not None:
        merged.append(_flush(buf))

    return merged


def _merge_stats(segments: list[dict]) -> dict:
    """Return before/after median and flush-reason breakdown for a segment list."""
    embed = [s for s in segments if s.get("type") in EMBED_TYPES]
    if not embed:
        return {}
    before_lens = [len(s["text"]) for s in embed]
    merged = _merge_short_segments(segments)
    after  = [s for s in merged if s.get("type") in EMBED_TYPES]
    after_lens = [len(s["text"]) for s in after]

    # flush-reason simulation
    flush = {"sentence_end": 0, "max_chars": 0, "final": 0}
    buf_text = None
    for s in embed:
        t = s["text"]
        if buf_text is None:
            buf_text = t
        elif len(buf_text) + 1 + len(t) <= MERGE_MAX_CHARS:
            buf_text = buf_text.rstrip() + " " + t.strip()
        else:
            flush["max_chars"] += 1
            buf_text = t
        if buf_text and len(buf_text) >= MERGE_MIN_CHARS and buf_text.rstrip()[-1:] in ".;:":
            flush["sentence_end"] += 1
            buf_text = None
    if buf_text:
        flush["final"] += 1

    return {
        "before_n":      len(before_lens),
        "after_n":       len(after_lens),
        "before_median": statistics.median(before_lens),
        "after_median":  statistics.median(after_lens),
        "flush_sentence_end_pct": flush["sentence_end"] / max(sum(flush.values()), 1) * 100,
        "flush_max_chars_pct":    flush["max_chars"]    / max(sum(flush.values()), 1) * 100,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Embedding client
# ──────────────────────────────────────────────────────────────────────────────

class EmbeddingClient:
    """
    Thin wrapper around LM Studio /v1/embeddings (OpenAI-compatible).
    Falls back to Ollama if LMSTUDIO_BASE_URL is unreachable.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model    = model
        self._client  = httpx.Client(timeout=timeout)
        self._dims: int | None = None

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of texts. Returns list of float vectors.
        Raises httpx.HTTPError on failure.
        """
        resp = self._client.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        # OpenAI format: {"data": [{"embedding": [...], "index": N}, ...]}
        items = sorted(data["data"], key=lambda x: x["index"])
        vectors = [item["embedding"] for item in items]
        if self._dims is None and vectors:
            self._dims = len(vectors[0])
        return vectors

    @property
    def dims(self) -> int:
        if self._dims is None:
            # Probe with empty string to discover dimensions
            self.embed_batch([""])
        return self._dims or DEFAULT_DIMS

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EmbeddingClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ping(self) -> bool:
        """Return True if the embedding endpoint is reachable."""
        try:
            self.embed_batch(["ping"])
            return True
        except Exception:
            return False


# ──────────────────────────────────────────────────────────────────────────────
# SQLite schema
# ──────────────────────────────────────────────────────────────────────────────

def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    # Load sqlite-vec extension
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def create_schema(con: sqlite3.Connection, dims: int) -> None:
    """
    Create all tables. Safe to call on an existing db (IF NOT EXISTS).
    dims must match the embedding model output dimensions.
    """
    con.executescript(f"""
        -- Metadata + full text
        CREATE TABLE IF NOT EXISTS segments (
            id                  TEXT PRIMARY KEY,
            norm                TEXT NOT NULL,
            version             TEXT,
            type                TEXT NOT NULL,
            page                INTEGER,
            anchor              TEXT,
            section             TEXT,
            section_title       TEXT,
            text                TEXT NOT NULL,
            normative_keywords  TEXT,   -- JSON array
            profile             TEXT,
            source_file         TEXT,   -- stem of the .segments.json file
            has_embedding       INTEGER DEFAULT 0
        );

        -- FTS5 index for BM25 keyword search
        CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts
        USING fts5(
            id UNINDEXED,
            text,
            norm UNINDEXED,
            section UNINDEXED,
            content='segments',
            content_rowid='rowid'
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
            INSERT INTO segments_fts(rowid, id, text, norm, section)
            VALUES (new.rowid, new.id, new.text, new.norm, new.section);
        END;
        CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
            INSERT INTO segments_fts(segments_fts, rowid, id, text, norm, section)
            VALUES ('delete', old.rowid, old.id, old.text, old.norm, old.section);
        END;
        CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
            INSERT INTO segments_fts(segments_fts, rowid, id, text, norm, section)
            VALUES ('delete', old.rowid, old.id, old.text, old.norm, old.section);
            INSERT INTO segments_fts(rowid, id, text, norm, section)
            VALUES (new.rowid, new.id, new.text, new.norm, new.section);
        END;

        -- Vector table (sqlite-vec)
        CREATE VIRTUAL TABLE IF NOT EXISTS segments_vec
        USING vec0(
            segment_id TEXT PRIMARY KEY,
            embedding  FLOAT[{dims}]
        );

        -- Index metadata (which files have been indexed, model used)
        CREATE TABLE IF NOT EXISTS index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    con.commit()


def drop_all(con: sqlite3.Connection) -> None:
    con.executescript("""
        DROP TABLE IF EXISTS segments;
        DROP TABLE IF EXISTS segments_fts;
        DROP TABLE IF EXISTS segments_vec;
        DROP TABLE IF EXISTS index_meta;
        DROP TRIGGER IF EXISTS segments_ai;
        DROP TRIGGER IF EXISTS segments_ad;
        DROP TRIGGER IF EXISTS segments_au;
    """)
    con.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Segment loading
# ──────────────────────────────────────────────────────────────────────────────

def iter_segment_files(segments_dir: Path) -> Iterator[Path]:
    if not segments_dir.is_dir():
        return
    yield from sorted(segments_dir.glob("*.segments.json"))


def load_segments_file(path: Path) -> tuple[str, str, list[dict]]:
    """
    Parse a .segments.json file and apply segment merging.
    Returns (norm, version, merged_segments_list).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    norm    = data.get("norm", path.stem)
    version = data.get("version", "")
    segs    = data.get("segments", [])
    segs    = _merge_short_segments(segs)   # ← merge fragments into full sentences
    return norm, version, segs


def already_indexed(con: sqlite3.Connection, source_file: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM index_meta WHERE key = ?",
        (f"indexed:{source_file}",),
    ).fetchone()
    return row is not None


def mark_indexed(con: sqlite3.Connection, source_file: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)",
        (f"indexed:{source_file}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )


def _reembed_missing_segments(
    source_file: str,
    con: sqlite3.Connection,
    client,  # EmbeddingClient | None
    verbose: bool = True,
) -> int:
    """
    For an already-indexed file, find all segments with has_embedding=0
    that belong to an EMBED_TYPE and (re-)embed them.
    Returns number of segments newly embedded.
    """
    if client is None:
        if verbose:
            print(f"  ⏭️  {source_file}  (already indexed, no embed client)")
        return 0

    rows = con.execute(
        "SELECT id, type, text FROM segments WHERE source_file = ? AND has_embedding = 0",
        (source_file,),
    ).fetchall()

    # Filter to embeddable types only
    to_embed = [
        (row["id"], row["text"])
        for row in rows
        if row["type"] in EMBED_TYPES and row["text"].strip()
    ]

    if not to_embed:
        if verbose:
            print(f"  ✅  {source_file}  (already indexed, all embeddings present)")
        return 0

    if verbose:
        print(f"  🔁  {source_file}  — {len(to_embed)} missing embedding(s), re-embedding …")

    embedded = 0
    for i in range(0, len(to_embed), EMBED_BATCH):
        batch = to_embed[i : i + EMBED_BATCH]
        ids   = [b[0] for b in batch]
        texts = [b[1] for b in batch]
        try:
            vectors = client.embed_batch(texts)
            for seg_id, vec in zip(ids, vectors):
                con.execute(
                    "INSERT OR REPLACE INTO segments_vec(segment_id, embedding) VALUES (?, ?)",
                    (seg_id, floats_to_blob(vec)),
                )
                con.execute(
                    "UPDATE segments SET has_embedding = 1 WHERE id = ?",
                    (seg_id,),
                )
            embedded += len(batch)
        except Exception as exc:
            print(f"  [WARN] Embedding batch failed for {source_file}: {exc}", file=sys.stderr)

    con.commit()
    if verbose and embedded:
        print(f"  ✅  {source_file}  — {embedded} embedding(s) added")
    return embedded


# ──────────────────────────────────────────────────────────────────────────────
# Vector serialisation
# ──────────────────────────────────────────────────────────────────────────────

def floats_to_blob(v: list[float]) -> bytes:
    """Pack float list to little-endian float32 blob for sqlite-vec."""
    return struct.pack(f"{len(v)}f", *v)


# ──────────────────────────────────────────────────────────────────────────────
# Core indexer
# ──────────────────────────────────────────────────────────────────────────────

def index_segments_file(
    path: Path,
    con: sqlite3.Connection,
    client: EmbeddingClient | None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Index a single .segments.json file into the database.
    Returns stats dict: {inserted, embedded, skipped, errors}.
    """
    norm, version, segments = load_segments_file(path)
    source_file = path.stem

    stats = {"inserted": 0, "embedded": 0, "skipped": 0, "errors": 0}

    # Collect segments to insert and segments needing embeddings
    to_insert: list[dict]  = []
    to_embed:  list[tuple[str, str]] = []  # (id, text)

    for seg in segments:
        seg_type = seg.get("type", "OTHER")
        if seg_type in SKIP_TYPES:
            stats["skipped"] += 1
            continue
        if seg_type not in EMBED_TYPES and seg_type not in FTS_ONLY_TYPES:
            stats["skipped"] += 1
            continue

        seg_id = seg["id"]

        # Check if already in db
        if not dry_run:
            exists = con.execute(
                "SELECT 1 FROM segments WHERE id = ?", (seg_id,)
            ).fetchone()
            if exists:
                stats["skipped"] += 1
                continue

        row = {
            "id":                 seg_id,
            "norm":               norm,
            "version":            version,
            "type":               seg_type,
            "page":               seg.get("page"),
            "anchor":             seg.get("anchor"),
            "section":            seg.get("section", ""),
            "section_title":      seg.get("section_title", ""),
            "text":               seg.get("text", ""),
            "normative_keywords": json.dumps(seg.get("normative_keywords", [])),
            "profile":            seg.get("profile", ""),
            "source_file":        source_file,
            "has_embedding":      0,
        }
        to_insert.append(row)
        if seg_type in EMBED_TYPES and seg.get("text", "").strip():
            to_embed.append((seg_id, seg["text"]))

    if dry_run:
        stats["inserted"] = len(to_insert)
        stats["embedded"] = len(to_embed)
        return stats

    # Insert metadata rows
    con.executemany(
        """
        INSERT OR IGNORE INTO segments
          (id, norm, version, type, page, anchor, section, section_title,
           text, normative_keywords, profile, source_file, has_embedding)
        VALUES
          (:id, :norm, :version, :type, :page, :anchor, :section,
           :section_title, :text, :normative_keywords, :profile,
           :source_file, :has_embedding)
        """,
        to_insert,
    )
    stats["inserted"] = len(to_insert)

    # Embed in batches
    if client and to_embed:
        for i in range(0, len(to_embed), EMBED_BATCH):
            batch = to_embed[i : i + EMBED_BATCH]
            ids   = [b[0] for b in batch]
            texts = [b[1] for b in batch]
            try:
                vectors = client.embed_batch(texts)
                for seg_id, vec in zip(ids, vectors):
                    con.execute(
                        "INSERT OR REPLACE INTO segments_vec(segment_id, embedding) VALUES (?, ?)",
                        (seg_id, floats_to_blob(vec)),
                    )
                    con.execute(
                        "UPDATE segments SET has_embedding = 1 WHERE id = ?",
                        (seg_id,),
                    )
                stats["embedded"] += len(batch)
            except Exception as exc:
                print(f"  [WARN] Embedding batch failed: {exc}", file=sys.stderr)
                stats["errors"] += len(batch)

    con.commit()
    mark_indexed(con, source_file)
    con.commit()

    if verbose:
        emb_label = f", {stats['embedded']} embedded" if client else ""
        print(
            f"  ✓  {source_file}  —  "
            f"{stats['inserted']} inserted"
            f"{emb_label}"
            f", {stats['skipped']} skipped"
        )

    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Check-merge mode  (--check-merge)
# ──────────────────────────────────────────────────────────────────────────────

def cmd_check_merge(segments_dir: Path) -> int:
    """
    Simulate _merge_short_segments() across all files without touching the DB.
    Prints per-file before/after medians and a corpus-wide summary.
    Exits 1 if corpus median after merge is below MERGE_TARGET_MEDIAN.
    """
    all_before: list[float] = []
    all_after:  list[float] = []

    print(f"\n{'File':<45} {'before':>7} {'after':>7}  {'Δ':>6}  flush%  max%")
    print("─" * 85)

    for f in sorted(segments_dir.glob("*.segments.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        segs = data.get("segments", [])
        st   = _merge_stats(segs)
        if not st:
            continue
        delta = st["after_median"] - st["before_median"]
        all_before.append(st["before_median"])
        all_after.append(st["after_median"])
        flag = "✓" if st["after_median"] >= MERGE_TARGET_MEDIAN else "⚠"
        print(
            f"  {flag} {f.stem[:43]:<43} "
            f"{st['before_median']:>7.0f} {st['after_median']:>7.0f} "
            f"  {delta:>+6.0f}  "
            f"{st['flush_sentence_end_pct']:>5.0f}%  "
            f"{st['flush_max_chars_pct']:>4.0f}%"
        )

    if not all_after:
        print("[ERROR] No segment files found.")
        return 1

    corpus_before = statistics.median(all_before)
    corpus_after  = statistics.median(all_after)
    ok = corpus_after >= MERGE_TARGET_MEDIAN

    print("─" * 85)
    print(
        f"  {'✓' if ok else '✗'} CORPUS  "
        f"before={corpus_before:.0f}  after={corpus_after:.0f}  "
        f"target≥{MERGE_TARGET_MEDIAN}"
    )
    if not ok:
        print(
            f"\n[FAIL] Post-merge median {corpus_after:.0f} < {MERGE_TARGET_MEDIAN}.\n"
            f"       Segments are too short — check pdf-segment.py output or\n"
            f"       lower MERGE_MIN_CHARS / raise MERGE_MAX_CHARS.\n"
        )
        return 1
    print(f"\n[OK]  Merge quality gate passed.\n")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Check-index mode  (--check-index)
# ──────────────────────────────────────────────────────────────────────────────

def cmd_check_index(segments_dir: Path, db_path: Path) -> int:
    """
    Verify that every *.segments.json file was fully indexed into the DB.

    Per-file checks:
      1. meta    — source_file appears in index_meta (file was processed)
      2. rows    — at least one segment row exists for that source_file
      3. count   — NORM/INFORM rows in DB >= expected after-merge count
      4. embeds  — embedding coverage >= EMBED_MIN_COVERAGE
                   (only checked when the DB has any embeddings at all)

    Exits 0 when every file passes, 1 otherwise.
    """
    if not db_path.is_file():
        print(f"[ERROR] DB not found: {db_path}")
        print(f"        Run: python3 scripts/build-index.py --rebuild")
        return 1

    con = _open_db(db_path)

    # Does the DB hold any embeddings at all?
    total_embedded = con.execute(
        "SELECT COUNT(*) FROM segments WHERE has_embedding = 1"
    ).fetchone()[0]
    check_embeds = total_embedded > 0

    seg_files = sorted(segments_dir.glob("*.segments.json"))
    if not seg_files:
        print(f"[ERROR] No segment files found in {segments_dir}")
        return 1

    col_embed  = f"emb≥{EMBED_MIN_COVERAGE:.0%}"
    print()
    print(
        f"  {'':1} {'File':<45} {'rows':>6} {'count':>6}"
        + (f"  {col_embed:>8}" if check_embeds else "")
        + "  meta"
    )
    print("─" * (85 + (11 if check_embeds else 0)))

    failures: list[str] = []

    for f in seg_files:
        stem = f.stem

        # expected counts from JSON (after merge)
        raw_data = json.loads(f.read_text(encoding="utf-8"))
        raw_segs = raw_data.get("segments", [])
        merged   = _merge_short_segments(raw_segs)
        expected_embed = sum(
            1 for s in merged
            if s.get("type") in EMBED_TYPES and s.get("text", "").strip()
        )

        # DB queries
        in_meta = con.execute(
            "SELECT 1 FROM index_meta WHERE key = ?",
            (f"indexed:{stem}",),
        ).fetchone() is not None

        db_total = con.execute(
            "SELECT COUNT(*) FROM segments WHERE source_file = ?", (stem,)
        ).fetchone()[0]

        db_embed_count = con.execute(
            "SELECT COUNT(*) FROM segments WHERE source_file = ? AND type IN ('NORM','INFORM')",
            (stem,),
        ).fetchone()[0]

        db_has_embed = con.execute(
            "SELECT COUNT(*) FROM segments WHERE source_file = ? AND has_embedding = 1",
            (stem,),
        ).fetchone()[0]

        # evaluate checks
        ok_rows  = db_total > 0
        ok_count = db_embed_count >= expected_embed
        ok_embed = True
        if check_embeds and db_embed_count > 0:
            ok_embed = (db_has_embed / db_embed_count) >= EMBED_MIN_COVERAGE
        elif check_embeds:
            ok_embed = False

        if not (in_meta and ok_rows and ok_count and ok_embed):
            failures.append(stem)

        # status icon
        if not in_meta or not ok_rows:
            icon = "✖"
        elif not ok_count or not ok_embed:
            icon = "⚠"
        else:
            icon = "✓"

        emb_pct   = f"{db_has_embed / db_embed_count:.0%}" if db_embed_count else "n/a"
        count_str = f"{db_embed_count}/{expected_embed}"
        embed_col = f"  {emb_pct:>8}" if check_embeds else ""
        meta_col  = f"  {'yes' if in_meta else 'NO'}"

        print(
            f"  {icon} {stem[:45]:<45} "
            f"{db_total:>6} {count_str:>6}"
            + embed_col
            + meta_col
        )

    con.close()

    print("─" * (85 + (11 if check_embeds else 0)))
    total  = len(seg_files)
    passed = total - len(failures)

    if failures:
        print(f"\n[✗] {len(failures)}/{total} file(s) FAILED index check:")
        for name in failures:
            print(f"     • {name}")
        print(
            f"\n    Fix: python3 scripts/build-index.py --rebuild\n"
            f"    Then re-run: python3 scripts/build-index.py --check-index\n"
        )
        return 1

    print(f"\n[✓] All {total} file(s) fully indexed.\n")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────────────────────

def print_stats(con: sqlite3.Connection) -> None:
    total = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    embedded = con.execute(
        "SELECT COUNT(*) FROM segments WHERE has_embedding = 1"
    ).fetchone()[0]
    print(f"\n[📊]  Index statistics")
    print(f"   Total segments : {total:>6}")
    print(f"   With embeddings: {embedded:>6}")
    print()
    rows = con.execute(
        """
        SELECT norm, version,
               COUNT(*) as total,
               SUM(CASE WHEN type='NORM' THEN 1 ELSE 0 END) as norm_count,
               SUM(has_embedding) as vecs
        FROM segments
        GROUP BY norm, version
        ORDER BY norm
        """
    ).fetchall()
    print(f"   {'Norm':<30} {'Ver':<10} {'Total':>6} {'NORM':>6} {'Vecs':>6}")
    print(f"   {'-'*30} {'-'*10} {'-'*6} {'-'*6} {'-'*6}")
    for r in rows:
        print(f"   {r['norm']:<30} {r['version']:<10} {r['total']:>6} "
              f"{r['norm_count']:>6} {r['vecs']:>6}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build SQLite FTS5 + vector index from segment corpus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/build-index.py                   # idempotent
  python3 scripts/build-index.py --rebuild         # drop & rebuild
  python3 scripts/build-index.py --stats           # show index stats
  python3 scripts/build-index.py --dry-run         # no db writes
  python3 scripts/build-index.py --check-merge     # simulate merge quality gate
  python3 scripts/build-index.py --check-index     # verify every doc is in DB
  python3 scripts/build-index.py --no-embed              # FTS-only (no LM Studio needed)
  python3 scripts/build-index.py --reembed-missing       # re-embed segments with has_embedding=0
  python3 scripts/build-index.py --model mxbai-embed-large
""",
    )
    parser.add_argument("--rebuild",      action="store_true", help="Drop and recreate the database")
    parser.add_argument("--stats",        action="store_true", help="Print index statistics and exit")
    parser.add_argument("--dry-run",      action="store_true", help="Parse and embed but do not write to db")
    parser.add_argument("--no-embed",     action="store_true", help="Skip embedding (FTS / BM25 only)")
    parser.add_argument("--reembed-missing", action="store_true", help="Re-embed segments where has_embedding=0 (skip already-embedded)")
    parser.add_argument("--check-merge",  action="store_true", help="Simulate merge, print quality gate, exit")
    parser.add_argument("--check-index",  action="store_true", help="Verify every segment file is fully in DB, exit")
    parser.add_argument("--model",        default=None,        help=f"Embedding model (default: {DEFAULT_MODEL})")
    parser.add_argument("--db",           default=None,        help=f"DB path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--segments",     default=None,        help=f"Segments dir (default: {SEGMENTS_DIR})")
    parser.add_argument("--quiet", "-q",  action="store_true")
    args = parser.parse_args()

    db_path      = Path(args.db or DEFAULT_DB_PATH)
    segments_dir = Path(args.segments or SEGMENTS_DIR)
    model        = args.model or DEFAULT_MODEL
    verbose      = not args.quiet

    if args.check_merge:
        sys.exit(cmd_check_merge(segments_dir))

    if args.check_index:
        sys.exit(cmd_check_index(segments_dir, db_path))

    if args.stats:
        if not db_path.is_file():
            sys.exit(f"[ERROR] DB not found: {db_path}\n        Run: python3 scripts/build-index.py")
        con = _open_db(db_path)
        print_stats(con)
        con.close()
        return

    seg_files = list(iter_segment_files(segments_dir))
    if not seg_files:
        sys.exit(
            f"[ERROR] No .segments.json files in {segments_dir}\n"
            "        Run: python3 scripts/pdf-segment.py"
        )

    if verbose:
        print(f"[INFO]  {len(seg_files)} segment file(s) found")
        print(f"[INFO]  DB: {db_path}")
        print(f"[INFO]  Model: {model}")
        reembed_label = " +--reembed-missing" if args.reembed_missing else ""
        print(f"[INFO]  Mode: {'--rebuild' if args.rebuild else '--dry-run' if args.dry_run else 'idempotent'}{reembed_label}\n")

    con = _open_db(db_path)

    if args.rebuild:
        if verbose:
            print("[⚠️]  --rebuild: dropping existing data")
        drop_all(con)

    client: EmbeddingClient | None = None
    if not args.no_embed and not args.dry_run:
        client = EmbeddingClient(base_url=DEFAULT_BASE_URL, model=model)
        if verbose:
            print(f"[🔌]  Pinging {DEFAULT_BASE_URL} … ", end="", flush=True)
        if client.ping():
            if verbose:
                print(f"OK  (dims={client.dims})")
            create_schema(con, client.dims)
        else:
            print(f"\n[⚠️]  LM Studio not reachable at {DEFAULT_BASE_URL}")
            print("     Continuing with FTS-only mode (no embeddings).")
            client = None
            create_schema(con, DEFAULT_DIMS)
    else:
        create_schema(con, DEFAULT_DIMS)

    total_stats = {"inserted": 0, "embedded": 0, "skipped": 0, "errors": 0}
    t0 = time.monotonic()

    for seg_file in seg_files:
        stem = seg_file.stem
        if not args.rebuild and not args.dry_run and already_indexed(con, stem):
            if args.reembed_missing:
                # File already indexed — but re-embed any segments with has_embedding=0
                n = _reembed_missing_segments(stem, con, client, verbose=verbose)
                total_stats["embedded"] += n
            else:
                if verbose:
                    print(f"  ⏭️  {stem}  (already indexed)")
            continue
        if verbose:
            print(f"[🔄]  {seg_file.name}")
        stats = index_segments_file(
            seg_file, con, client,
            dry_run=args.dry_run,
            verbose=verbose,
        )
        for k in total_stats:
            total_stats[k] += stats[k]

    elapsed = time.monotonic() - t0
    if client:
        client.close()
    con.close()

    if verbose:
        print(f"\n[✅]  Done in {elapsed:.1f}s")
        print(f"   Inserted : {total_stats['inserted']}")
        print(f"   Embedded : {total_stats['embedded']}")
        print(f"   Skipped  : {total_stats['skipped']}")
        if total_stats['errors']:
            print(f"   Errors   : {total_stats['errors']}")
        if not args.dry_run:
            print(f"   DB       : {db_path}")


if __name__ == "__main__":
    main()
