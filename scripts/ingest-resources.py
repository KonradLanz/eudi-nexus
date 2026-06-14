#!/usr/bin/env python3
"""
ingest-resources.py  —  Ingest public external resources into the corpus.

Reads downloads/stf705_public_resources.json and writes one corpus JSON
per entry into corpus/resources/.  Each entry gets a stable ID derived
from its own "id" field, so re-runs are fully idempotent.

Corpus schema (subset of the spec corpus schema, augmented for resources):
  {
    "id":           str,          # stable slug, e.g. "stf705-tor"
    "type":         str,          # tor | portal | regulation | contributions | ...
    "label":        str,          # human-readable title
    "url":          str,          # canonical public URL
    "format":       str,          # html | pdf | docx | github
    "scope":        str,          # stf | tc | external
    "stf":          str|null,     # e.g. "STF705"
    "wki_ids":      list[str],    # ETSI Work Item IDs this resource belongs to
    "note":         str,          # freetext description for AI retrieval
    "source_file":  str,          # which JSON it came from
    "ingested_at":  str           # ISO timestamp
  }

Usage:
    python scripts/ingest-resources.py
    python scripts/ingest-resources.py --force
    python scripts/ingest-resources.py --resources downloads/my_resources.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT   = Path(__file__).parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "downloads" / "stf705_public_resources.json"
CORPUS_DIR     = PROJECT_ROOT / "corpus" / "resources"


def corpus_path(resource_id: str) -> Path:
    return CORPUS_DIR / f"{resource_id}.json"


def is_up_to_date(source_file: Path, resource_id: str) -> bool:
    """Skip if corpus JSON is newer than the source resources file."""
    out = corpus_path(resource_id)
    if not out.exists():
        return False
    return out.stat().st_mtime >= source_file.stat().st_mtime


def ingest_resource(entry: dict, source_file: Path) -> dict:
    """Enrich a raw resource entry with corpus metadata."""
    return {
        "id":          entry["id"],
        "type":        entry.get("type", "unknown"),
        "label":       entry.get("label", ""),
        "url":         entry.get("url", ""),
        "format":      entry.get("format", "html"),
        "scope":       entry.get("scope", "external"),
        "stf":         entry.get("stf"),
        "wki_ids":     entry.get("wki_ids", []),
        "note":        entry.get("note", ""),
        "source_file": source_file.name,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest public resources into corpus.")
    parser.add_argument(
        "--resources",
        default=str(DEFAULT_SOURCE),
        help=f"Path to resources JSON (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--corpus",
        default=str(CORPUS_DIR),
        help=f"Output corpus directory (default: {CORPUS_DIR})",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Reprocess even if corpus JSON is already up-to-date",
    )
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    source_file = Path(args.resources)
    corpus_dir  = Path(args.corpus)
    verbose     = not args.quiet

    if not source_file.exists():
        raise SystemExit(f"[ERROR] Resources file not found: {source_file}")

    corpus_dir.mkdir(parents=True, exist_ok=True)

    entries = json.loads(source_file.read_text(encoding="utf-8"))
    ok = skipped = errors = 0

    for entry in entries:
        rid = entry.get("id")
        if not rid:
            print(f"[WARN] Entry without 'id' — skipping: {entry}", flush=True)
            errors += 1
            continue

        if not args.force and is_up_to_date(source_file, rid):
            if verbose:
                print(f"[SKIP] {rid}")
            skipped += 1
            continue

        try:
            corpus_entry = ingest_resource(entry, source_file)
            out_path = corpus_dir / f"{rid}.json"
            out_path.write_text(
                json.dumps(corpus_entry, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if verbose:
                print(f"[OK]   {rid}  →  {out_path.relative_to(PROJECT_ROOT)}")
            ok += 1
        except Exception as exc:
            print(f"[ERROR] {rid}: {exc}", flush=True)
            errors += 1

    if verbose:
        print()
        print(
            f"[DONE] {ok} ingested | {skipped} skipped | {errors} errors"
            f"  (of {len(entries)} total)"
        )


if __name__ == "__main__":
    main()
