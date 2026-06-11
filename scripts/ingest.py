#!/usr/bin/env python3
"""
ingest.py  —  Smart orchestrator: chooses DOCX or PDF per document.

Logic per document:
  1. If a .format-comparison.json sidecar exists alongside the PDF:
       recommendation == 'docx'  AND docxPath exists  →  ingest DOCX
       otherwise                                       →  ingest PDF
  2. No sidecar → ingest PDF as before (full backward compat)

This means:
  - Users WITHOUT ETSI credentials  →  all PDFs, same as before
  - Users WITH credentials          →  DOCX where available, PDF as fallback

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
import subprocess
import sys
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


def resolve_source(pdf_path: Path, force_format: str | None) -> tuple[Path, str]:
    """
    Return (source_path, format) — either the PDF or its paired DOCX.

    format_format: None | 'pdf' | 'docx'
    """
    if force_format == "pdf":
        return pdf_path, "pdf"

    cmp = load_comparison(pdf_path)

    if force_format == "docx":
        if cmp and cmp.get("docxPath"):
            candidate = PROJECT_ROOT / cmp["docxPath"]
            if candidate.exists():
                return candidate, "docx"
        # Try sibling .docx
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


# ─── Collect PDFs (entry points) ─────────────────────────────────────────────

def collect_pdfs(inputs: list[str]) -> list[Path]:
    result: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            for f in sorted(p.rglob("*.pdf")):
                if not f.name.startswith((".", "_")):
                    result.append(f)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            result.append(p)
        else:
            print(f"[WARN] {inp} is not a PDF or directory — skipped.", file=sys.stderr)
    return result


# ─── Ingest dispatch ─────────────────────────────────────────────────────────

def run_ingest(source: Path, fmt: str, corpus_root: Path, titles_dir: Path, verbose: bool):
    """Dispatch to the appropriate ingest script as a subprocess."""
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
    result = subprocess.run(cmd, capture_output=not verbose, text=True)
    if result.returncode != 0 and not verbose:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


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
        help=f"PDF files or directories to scan (default: {SPECS_DIR})",
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
    args = parser.parse_args()

    corpus_root = Path(args.corpus)
    titles_dir  = Path(args.titles)
    verbose     = not args.quiet

    force_format = None
    if args.pdf_only and args.docx_only:
        sys.exit("[ERROR] --pdf-only and --docx-only are mutually exclusive.")
    if args.pdf_only:  force_format = "pdf"
    if args.docx_only: force_format = "docx"

    pdf_files = collect_pdfs(args.input)
    if not pdf_files:
        sys.exit("[ERROR] No PDF files found.")

    if verbose:
        mode = "--force" if args.force else "idempotent (mtime)"
        fmt  = force_format or "smart (DOCX preferred when available)"
        print(f"[INFO] {len(pdf_files)} documents found | mode: {mode} | format: {fmt}")
        print()

    ok = skipped = errors = docx_used = pdf_used = 0

    for pdf in pdf_files:
        source, fmt = resolve_source(pdf, force_format)

        # Skip if corpus JSON is newer than source (idempotency)
        if not args.force and is_up_to_date(source, corpus_root):
            if verbose:
                tag = "DOCX" if fmt == "docx" else "PDF "
                print(f"[SKIP-{tag}] {source.name}")
            skipped += 1
            continue

        if fmt == "docx":
            docx_used += 1
            tag = "DOCX"
        else:
            pdf_used  += 1
            tag = "PDF "

        if verbose:
            print(f"[{tag}] {source.name}")

        success = run_ingest(source, fmt, corpus_root, titles_dir, verbose)
        if success:
            ok += 1
        else:
            errors += 1
            print(f"[ERROR] Failed: {source.name}", file=sys.stderr)

    if verbose:
        print()
        print(f"[DONE] {ok} ingested ({docx_used} DOCX / {pdf_used} PDF) | "
              f"{skipped} skipped | {errors} errors  (of {len(pdf_files)} total)")


if __name__ == "__main__":
    main()
