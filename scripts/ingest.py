#!/usr/bin/env python3
"""
ingest.py  —  Smart orchestrator: chooses DOCX or PDF per document.

Logic per document:
  Priority 1 — .format-comparison.json sidecar exists:
       recommendation == 'docx'  AND docxPath exists  →  ingest DOCX
       otherwise                                       →  ingest PDF

  Priority 2 — standalone DOCX (no paired PDF on disk, i.e. draft-only from docbox):
       ingest DOCX directly

  Priority 3 — no sidecar, no standalone DOCX → ingest PDF

This means:
  - Users WITHOUT ETSI credentials  →  all PDFs, same as before
  - Users WITH credentials          →  DOCX where available, PDF as fallback
  - Draft DOCX with no PDF at all   →  ingested as DOCX-only entry

Idempotency:
  - Skips documents whose corpus JSON is already up-to-date (mtime check)
  - --force overwrites everything
  - --pdf-only / --docx-only override format selection

Usage:
    python scripts/ingest.py                      # smart mode, all documents
    python scripts/ingest.py --force              # reprocess everything
    python scripts/ingest.py --pdf-only           # always use PDF
    python scripts/ingest.py --docx-only          # always use DOCX (skip if no .docx)
    python scripts/ingest.py downloads/specs/TS/  # only one subdirectory

Or via npm:
    npm run ingest
"""

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SPECS_DIR    = PROJECT_ROOT / "downloads" / "specs"
CORPUS_ROOT  = PROJECT_ROOT / "corpus"
TITLES_DIR   = SPECS_DIR / "_titles"


# ─── Sidecar helpers ─────────────────────────────────────────────────────────

def load_comparison(pdf_path: Path) -> dict | None:
    """Load .format-comparison.json sidecar if it exists next to the PDF."""
    sidecar = pdf_path.with_suffix(".format-comparison.json")
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def resolve_source(entry: Path, force_format: str | None) -> tuple[Path, str]:
    """
    Given either a PDF or a DOCX entry path, return (source_path, format).

    Entry may be:
      - a .pdf  → classic path: check sidecar for paired DOCX
      - a .docx → standalone DOCX (no paired PDF), use directly

    force_format: None | 'pdf' | 'docx'
    """
    suffix = entry.suffix.lower()

    # ── Standalone DOCX (no PDF sibling) ──────────────────────────────────
    if suffix == ".docx":
        if force_format == "pdf":
            # Try to find a sibling PDF with the same stem
            candidate = entry.with_suffix(".pdf")
            if candidate.exists():
                return candidate, "pdf"
            # No PDF available — skip this entry in pdf-only mode
            return entry, "skip"
        return entry, "docx"

    # ── PDF entry ─────────────────────────────────────────────────────────
    pdf_path = entry

    if force_format == "pdf":
        return pdf_path, "pdf"

    cmp = load_comparison(pdf_path)

    if force_format == "docx":
        if cmp and cmp.get("docxPath"):
            candidate = PROJECT_ROOT / cmp["docxPath"]
            if candidate.exists():
                return candidate, "docx"
        # Try sibling .docx (same stem)
        candidate = pdf_path.with_suffix(".docx")
        if candidate.exists():
            return candidate, "docx"
        return pdf_path, "pdf"   # no DOCX available — fall back silently

    # Smart mode: use comparison sidecar recommendation
    if cmp and cmp.get("recommendation") == "docx" and cmp.get("docxPath"):
        candidate = PROJECT_ROOT / cmp["docxPath"]
        if candidate.exists():
            return candidate, "docx"

    return pdf_path, "pdf"


# ─── Up-to-date check ────────────────────────────────────────────────────────

def corpus_json_path(source: Path, corpus_root: Path) -> Path:
    return corpus_root / "specs" / (source.stem + ".json")


def is_up_to_date(source: Path, corpus_root: Path) -> bool:
    """
    True if the corpus JSON exists AND is newer than the source document.
    This is the idempotency guard — avoids re-ingesting unchanged files.
    """
    out = corpus_json_path(source, corpus_root)
    if not out.exists():
        return False
    return out.stat().st_mtime >= source.stat().st_mtime


# ─── Collect documents (entry points) ────────────────────────────────────────

def collect_docs(inputs: list[str], force_format: str | None) -> list[Path]:
    """
    Collect all documents to process.

    Strategy:
      - Always collect *.pdf as primary entry points.
      - Also collect *.docx that have NO sibling *.pdf with the same stem
        (i.e. standalone DOCX-only documents — draft specs from docbox).
      - In --pdf-only mode: skip standalone DOCX entries entirely.
      - Skip files starting with '.' or '_' (sidecars, system files).

    This ensures docs downloaded exclusively as DOCX (no PDF on deliver/)
    are never silently dropped from the ingest pipeline.
    """
    result:  list[Path] = []
    seen_stems: set[str] = set()

    def _scan(directory: Path) -> None:
        # First pass: collect all PDF files
        for f in sorted(directory.rglob("*.pdf")):
            if f.name.startswith((".", "_")):
                continue
            result.append(f)
            seen_stems.add(f.stem.lower())

        # Second pass: standalone DOCX (no paired PDF)
        if force_format == "pdf":
            return  # pdf-only mode: don't bother with DOCX-only entries
        for f in sorted(directory.rglob("*.docx")):
            if f.name.startswith((".", "_")):
                continue
            if f.stem.lower() in seen_stems:
                continue  # paired PDF already collected — sidecar will handle format choice
            result.append(f)

    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            _scan(p)
        elif p.is_file() and p.suffix.lower() in (".pdf", ".docx"):
            result.append(p)
        else:
            print(f"[WARN] {inp} is not a PDF/DOCX or directory — skipped.",
                  file=sys.stderr)
    return result


# ─── Ingest dispatch ─────────────────────────────────────────────────────────

_print_lock = threading.Lock()


def run_ingest(source: Path, fmt: str, corpus_root: Path, titles_dir: Path, verbose: bool) -> tuple[bool, str]:
    """Dispatch to the appropriate ingest script as a subprocess.

    Always captures output internally so parallel runs don't interleave.
    Returns (success, buffered_output).
    """
    script = Path(__file__).parent / ("pdf-ingest.py" if fmt == "pdf" else "docx-ingest.py")
    cmd = [
        sys.executable, str(script),
        str(source),
        "--corpus", str(corpus_root),
        "--titles", str(titles_dir),
        "--force",
    ]
    if not verbose:
        cmd.append("--quiet")
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Collect all output into a single string to print atomically
    lines = []
    if result.stdout.strip():
        lines.append(result.stdout.rstrip())
    if result.returncode != 0 and result.stderr.strip():
        lines.append(result.stderr.rstrip())
    return result.returncode == 0, "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart ingest orchestrator: DOCX when available, PDF as fallback.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/ingest.py                       # all docs, smart format selection
  python scripts/ingest.py --force               # reprocess everything
  python scripts/ingest.py --pdf-only            # always PDF (for comparison)
  python scripts/ingest.py downloads/specs/TS/   # only TS subdirectory
""",
    )
    parser.add_argument(
        "input", nargs="*",
        default=[str(SPECS_DIR)],
        help=f"PDF/DOCX files or directories to scan (default: {SPECS_DIR})",
    )
    parser.add_argument("--corpus", default=str(CORPUS_ROOT))
    parser.add_argument("--titles", default=str(TITLES_DIR))
    parser.add_argument("--force", "-f", action="store_true",
                        help="Reprocess even if corpus JSON is up-to-date")
    parser.add_argument("--pdf-only", action="store_true",
                        help="Always use PDF, ignore DOCX sidecars")
    parser.add_argument("--docx-only", action="store_true",
                        help="Always prefer DOCX; skip docs with no DOCX available")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument(
        "--workers", "-j", type=int, default=min(4, os.cpu_count() or 1),
        metavar="N",
        help="Parallel worker threads (default: min(4, cpu_count))",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus)
    titles_dir  = Path(args.titles)
    verbose     = not args.quiet

    force_format = None
    if args.pdf_only and args.docx_only:
        sys.exit("[ERROR] --pdf-only and --docx-only are mutually exclusive.")
    if args.pdf_only:  force_format = "pdf"
    if args.docx_only: force_format = "docx"

    docs = collect_docs(args.input, force_format)
    if not docs:
        sys.exit("[ERROR] No documents found.")

    if verbose:
        mode = "--force" if args.force else "idempotent (mtime)"
        fmt  = force_format or "smart (DOCX preferred when available)"
        print(f"[INFO] {len(docs)} documents found | mode: {mode} | format: {fmt} | workers: {args.workers}")
        print()

    ok = skipped = errors = docx_used = pdf_used = 0

    # ── Phase 1: resolve + skip synchronously (fast, no I/O) ──────────────
    pending: list[tuple[Path, str]] = []   # (source, fmt) to actually process

    for entry in docs:
        source, fmt = resolve_source(entry, force_format)

        if fmt == "skip":
            if verbose:
                print(f"[SKIP-DOCX-ONLY] {entry.name} (no PDF available, --pdf-only)")
            skipped += 1
            continue

        if not args.force and is_up_to_date(source, corpus_root):
            if verbose:
                tag = "DOCX" if fmt == "docx" else "PDF "
                print(f"[SKIP-{tag}] {source.name}")
            skipped += 1
            continue

        pending.append((source, fmt))

    if not pending:
        if verbose:
            print()
            print(f"[DONE] nothing to do — {skipped} skipped | 0 errors  (of {len(docs)} total)")
        return

    if verbose and skipped:
        print()

    # ── Phase 2: parallel ingest ───────────────────────────────────────────
    def _ingest_job(source: Path, fmt: str) -> tuple[Path, str, bool, str]:
        """Worker: run ingest, return (source, fmt, success, output)."""
        success, output = run_ingest(source, fmt, corpus_root, titles_dir, verbose)
        return source, fmt, success, output

    futures = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for source, fmt in pending:
            tag = "DOCX" if fmt == "docx" else "PDF "
            if verbose:
                with _print_lock:
                    print(f"[{tag}→] {source.name}")
            futures[pool.submit(_ingest_job, source, fmt)] = (source, fmt)

        for future in as_completed(futures):
            source, fmt, success, output = future.result()
            tag = "DOCX" if fmt == "docx" else "PDF "
            if fmt == "docx":
                docx_used += 1
            else:
                pdf_used += 1

            if success:
                ok += 1
                status = f"[{tag}✓] {source.name}"
            else:
                errors += 1
                status = f"[{tag}✗] {source.name}"

            with _print_lock:
                if verbose:
                    if output:
                        # Print buffered subprocess output indented under the status line
                        indented = "\n".join(f"    {l}" for l in output.splitlines())
                        print(f"{status}\n{indented}")
                    else:
                        print(status)
                elif not success:
                    if output:
                        print(output, file=sys.stderr)
                    print(f"[ERROR] Failed: {source.name}", file=sys.stderr)

    if verbose:
        print()
        print(f"[DONE] {ok} ingested ({docx_used} DOCX / {pdf_used} PDF) | "
              f"{skipped} skipped | {errors} errors  (of {len(docs)} total)")


if __name__ == "__main__":
    main()
