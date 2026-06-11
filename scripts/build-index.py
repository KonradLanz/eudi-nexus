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
EMBED_TYPES   = {"NORM", "INFORM"}
# Segment types to index FTS-only (no vector)
FTS_ONLY_TYPES = {"SECTION"}
# Segment types to skip entirely
SKIP_TYPES    = {"HEADER", "FOOTER", "TOC", "OTHER"}

# Batch size for embedding API calls
EMBED_BATCH = 16


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
    Parse a .segments.json file.
    Returns (norm, version, segments_list).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    norm    = data.get("norm", path.stem)
    version = data.get("version", "")
    segs    = data.get("segments", [])
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
  python3 scripts/build-index.py                  # idempotent
  python3 scripts/build-index.py --rebuild        # drop & rebuild
  python3 scripts/build-index.py --stats          # show index stats
  python3 scripts/build-index.py --dry-run        # no db writes
  python3 scripts/build-index.py --no-embed       # FTS-only (no LM Studio needed)
  python3 scripts/build-index.py --model mxbai-embed-large
""",
    )
    parser.add_argument("--rebuild",  action="store_true", help="Drop and recreate the database")
    parser.add_argument("--stats",    action="store_true", help="Print index statistics and exit")
    parser.add_argument("--dry-run",  action="store_true", help="Parse and embed but do not write to db")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding (FTS / BM25 only)")
    parser.add_argument("--model",    default=None,        help=f"Embedding model (default: {DEFAULT_MODEL})")
    parser.add_argument("--db",       default=None,        help=f"DB path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--segments", default=None,        help=f"Segments dir (default: {SEGMENTS_DIR})")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    db_path      = Path(args.db or DEFAULT_DB_PATH)
    segments_dir = Path(args.segments or SEGMENTS_DIR)
    model        = args.model or DEFAULT_MODEL
    verbose      = not args.quiet

    # ── Stats-only mode ────────────────────────────────────────────
    if args.stats:
        if not db_path.is_file():
            sys.exit(f"[ERROR] DB not found: {db_path}\n        Run: python3 scripts/build-index.py")
        con = _open_db(db_path)
        print_stats(con)
        con.close()
        return

    # ── Discover segment files ────────────────────────────────────────
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
        print(f"[INFO]  Mode: {'--rebuild' if args.rebuild else '--dry-run' if args.dry_run else 'idempotent'}\n")

    # ── Open / reset DB ────────────────────────────────────────────
    con = _open_db(db_path)

    if args.rebuild:
        if verbose:
            print("[⚠️]  --rebuild: dropping existing data")
        drop_all(con)

    # ── Embedding client ───────────────────────────────────────────
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

    # ── Index each file ────────────────────────────────────────────
    total_stats = {"inserted": 0, "embedded": 0, "skipped": 0, "errors": 0}
    t0 = time.monotonic()

    for seg_file in seg_files:
        stem = seg_file.stem
        if not args.rebuild and not args.dry_run and already_indexed(con, stem):
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
