#!/usr/bin/env python3
"""
toc-extract.py  —  Extract Table of Contents from all ETSI PDFs

Scans every PDF in downloads/specs/ and extracts:
  - Cover page metadata (title, reference, keywords, date)
  - Full TOC entries (section number + title + page)
  - Pre-body boilerplate sections (IPR, Modal verbs, etc.)
  - First real section heading (where the normative content starts)

Output:
  corpus/toc/                         — one JSON per PDF
  corpus/toc/_summary.json            — all TOCs merged, sorted by norm
  corpus/toc/_structure-report.md     — human-readable structure analysis

Usage:
  python3 scripts/toc-extract.py                    # all PDFs
  python3 scripts/toc-extract.py --pdf downloads/specs/EN/en_319403v020202p.pdf
  python3 scripts/toc-extract.py --report           # only print summary report
  python3 scripts/toc-extract.py --limit 5          # first 5 PDFs only
  python3 scripts/toc-extract.py --timeout 60       # seconds per PDF (default 30)

npm:
  "toc":        ".venv/bin/python3 scripts/toc-extract.py",
  "toc:report": ".venv/bin/python3 scripts/toc-extract.py --report",
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

try:
    import pdfplumber
except ImportError:
    sys.exit("[ERROR] pdfplumber not installed — run: pip install pdfplumber")


# ──────────────────────────────────────────────────────────────────────────────
# Sidecar detection
# ──────────────────────────────────────────────────────────────────────────────

def _is_sidecar(path: Path) -> bool:
    """Return True for hidden/sidecar files that are not real PDFs."""
    return path.name.startswith(".")


# ──────────────────────────────────────────────────────────────────────────────
# Regexes
# ──────────────────────────────────────────────────────────────────────────────

# TOC entry with dot-leader: "5.3  General requirements ......... 42"
_TOC_LINE_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)*\d+\.?)\s{1,6}(?P<title>[^\n]{2,80})"
    r"[.\s]{3,}(?P<page>\d{1,4})\s*$"
)

# Looser: section number + spaces + title + spaces + page (no dot-leader)
_TOC_LOOSE_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)*\d+\.?)\s{2,}(?P<title>.{2,60}?)\s{2,}(?P<page>\d{1,4})\s*$"
)

# Annex entry: "Annex A (normative):  Title ......... 55"
_TOC_ANNEX_RE = re.compile(
    r"^Annex\s+(?P<letter>[A-Z])\s*\((?P<type>normative|informative)\)[:\s]*"
    r"(?P<title>.{0,120}?)[.\s]{0,30}(?P<page>\d{1,4})\s*$",
    re.IGNORECASE,
)

# Boilerplate entries in TOC without a section number:
# "Intellectual Property Rights.....5", "Foreword.....5", "History.....51"
_TOC_BOILERPLATE_RE = re.compile(
    r"^(?P<title>Intellectual Property Rights|Foreword|Introduction|History"
    r"|Modal verbs terminology)[.\s]{3,}(?P<page>\d{1,4})\s*$",
    re.IGNORECASE,
)

# Cover page date
_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}\b"
)

# Known boilerplate section titles (appear after TOC, before real content)
_BOILERPLATE_HEADINGS = {
    "intellectual property rights",
    "foreword",
    "modal verbs terminology",
    "introduction",
}

# First real normative section
_SCOPE_RE = re.compile(r"^1\.?\s+Scope\b", re.IGNORECASE)

# Dot-leader detector used in _page_text
_DOT_LEADER_RE = re.compile(r"[.]{3,}|(?:[. ]{2}){2,}")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _page_text(page) -> str:
    """
    Extract text from a pdfplumber page.

    Lines containing a dot-leader are kept verbatim so _TOC_LINE_RE can match.
    All other lines have runs of 3+ spaces collapsed to 2 spaces.
    """
    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _DOT_LEADER_RE.search(line):
            lines.append(line)
        else:
            lines.append(re.sub(r" {3,}", "  ", line))
    return "\n".join(lines)


def _is_toc_line(line: str) -> dict | None:
    """Return parsed TOC entry dict or None."""
    m = _TOC_LINE_RE.match(line)
    if m:
        return {
            "num":   m.group("num").rstrip("."),
            "title": m.group("title").strip().rstrip(". "),
            "page":  int(m.group("page")),
        }
    m = _TOC_ANNEX_RE.match(line)
    if m:
        return {
            "num":        f"Annex {m.group('letter')}",
            "title":      m.group("title").strip().rstrip(". "),
            "page":       int(m.group("page")),
            "annex_type": m.group("type").lower(),
        }
    m = _TOC_BOILERPLATE_RE.match(line)
    if m:
        return {
            "num":        "",
            "title":      m.group("title").strip(),
            "page":       int(m.group("page")),
            "boilerplate": True,
        }
    m = _TOC_LOOSE_RE.match(line)
    if m:
        return {
            "num":   m.group("num").rstrip("."),
            "title": m.group("title").strip(),
            "page":  int(m.group("page")),
        }
    return None


def _looks_like_toc_page(lines: list[str], threshold: int = 3) -> bool:
    """Return True if the page has enough TOC-style lines."""
    return sum(1 for ln in lines if _is_toc_line(ln)) >= threshold


# ──────────────────────────────────────────────────────────────────────────────
# Per-PDF extractor
# ──────────────────────────────────────────────────────────────────────────────

def extract_toc(pdf_path: Path) -> dict:
    stem = pdf_path.stem
    result = {
        "stem":             stem,
        "pdf":              str(pdf_path),
        "title":            "",
        "reference":        stem,
        "keywords":         [],
        "date":             "",
        "toc":              [],
        "boilerplate":      [],
        "body_starts_page": None,
        "total_pages":      0,
        "toc_pages":        [],
    }

    with pdfplumber.open(str(pdf_path)) as pdf:
        result["total_pages"] = len(pdf.pages)
        pages_text = [(p.page_number, _page_text(p)) for p in pdf.pages]

        # ── Cover page ────────────────────────────────────────────
        if pages_text:
            cover_lines = pages_text[0][1].splitlines()
            for line in cover_lines:
                if line and not re.match(r"^(ETSI|Draft|Final|V\d|EN\s|TS\s|TR\s)", line):
                    result["title"] = line
                    break
            dm = _DATE_RE.search(pages_text[0][1])
            if dm:
                result["date"] = dm.group(0)

        # ── Find TOC pages ───────────────────────────────────────
        toc_entries: list[dict] = []
        toc_page_nums: list[int] = []
        in_toc = False

        for page_nr, text in pages_text:
            lines = text.splitlines()
            is_toc = _looks_like_toc_page(lines)

            if is_toc:
                in_toc = True
                toc_page_nums.append(page_nr)
                for line in lines:
                    entry = _is_toc_line(line)
                    if entry:
                        toc_entries.append(entry)
            elif in_toc:
                grace_entries = [e for e in (_is_toc_line(l) for l in lines) if e]
                if grace_entries:
                    toc_page_nums.append(page_nr)
                    toc_entries.extend(grace_entries)
                else:
                    break

        result["toc"] = toc_entries
        result["toc_pages"] = toc_page_nums

        # ── Boilerplate + body start ─────────────────────────────
        post_toc_start = max(toc_page_nums) + 1 if toc_page_nums else 4
        boilerplate_found: list[str] = []

        for page_nr, text in pages_text:
            if page_nr < post_toc_start:
                continue
            for line in text.splitlines():
                lower = line.lower().strip()
                if lower in _BOILERPLATE_HEADINGS:
                    if lower not in [b.lower() for b in boilerplate_found]:
                        boilerplate_found.append(line.strip())
                if _SCOPE_RE.match(line.strip()):
                    result["body_starts_page"] = page_nr
                    break
            if result["body_starts_page"]:
                break

        result["boilerplate"] = boilerplate_found

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Timeout wrapper (multiprocessing)
#
# pdfplumber can hang indefinitely on certain corrupt or encrypted PDFs.
# We run extract_toc() in a child process and kill it if it exceeds the
# timeout.  The child writes the result to a shared Queue; the parent reads
# it with a timeout.  On timeout the child process is terminated and the
# batch loop records a "timed out" error instead of hanging forever.
# ──────────────────────────────────────────────────────────────────────────────

def _worker(pdf_path_str: str, queue: multiprocessing.Queue) -> None:
    """Child process: run extract_toc and put result (or exception) on queue."""
    try:
        result = extract_toc(Path(pdf_path_str))
        queue.put(("ok", result))
    except Exception as exc:  # noqa: BLE001
        queue.put(("err", str(exc)))


def extract_toc_with_timeout(pdf_path: Path, timeout: int) -> dict:
    """
    Run extract_toc() in a subprocess with a hard wall-clock timeout.
    Raises TimeoutError if the PDF takes longer than `timeout` seconds.
    """
    ctx = multiprocessing.get_context("fork")  # faster on macOS/Linux
    queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(str(pdf_path), queue), daemon=True)
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
        raise TimeoutError(f"timed out after {timeout}s")

    if queue.empty():
        raise RuntimeError("worker exited without result (crash or OOM)")

    status, payload = queue.get_nowait()
    if status == "err":
        raise RuntimeError(payload)
    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Structure report
# ──────────────────────────────────────────────────────────────────────────────

def build_report(all_tocs: list[dict]) -> str:
    lines = [
        "# ETSI Corpus — Document Structure Report",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Documents: {len(all_tocs)}",
        "",
    ]

    top_sections: dict[str, int] = defaultdict(int)
    for doc in all_tocs:
        for entry in doc["toc"]:
            num = entry["num"]
            if re.match(r"^\d+$", num) or re.match(r"^Annex\s+[A-Z]$", num, re.I):
                top_sections[entry["title"]] += 1

    lines += ["## Top-level sections (frequency across all documents)", ""]
    for title, count in sorted(top_sections.items(), key=lambda x: -x[1])[:40]:
        lines.append(f"  {count:4d}×  {title}")
    lines.append("")

    bp_counts: dict[str, int] = defaultdict(int)
    for doc in all_tocs:
        for bp in doc["boilerplate"]:
            bp_counts[bp] += 1

    lines += ["## Boilerplate sections between TOC and body", ""]
    for title, count in sorted(bp_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {count:4d}×  {title}")
    lines.append("")

    starts = [d["body_starts_page"] for d in all_tocs if d["body_starts_page"]]
    if starts:
        lines += ["## Body start page distribution", ""]
        from collections import Counter
        for page, count in sorted(Counter(starts).items()):
            lines.append(f"  page {page:3d}:  {count:3d} documents")
        lines.append("")

    lines += ["## Per-document TOC (top-level only)", ""]
    for doc in sorted(all_tocs, key=lambda d: d["stem"]):
        top = [
            e for e in doc["toc"]
            if re.match(r"^\d+$", e["num"]) or re.match(r"^Annex\s+[A-Z]$", e["num"], re.I)
        ]
        body_p = doc["body_starts_page"] or "?"
        lines.append(f"### {doc['stem']}  (body p.{body_p}, {doc['total_pages']} pp.)")
        for e in top:
            annex_tag = f" ({e.get('annex_type', '')})" if "annex_type" in e else ""
            lines.append(f"  {e['num']:12}  {e['title']}{annex_tag}  → p.{e['page']}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _find_pdfs(root: Path) -> list[Path]:
    """Recursively find all real PDFs, skipping sidecar/hidden files."""
    return sorted(p for p in root.rglob("*.pdf") if not _is_sidecar(p))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract TOC structure from ETSI PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pdf",     help="Single PDF to process")
    parser.add_argument("--input",   default="downloads/specs",
                        help="Root dir with PDFs (default: downloads/specs)")
    parser.add_argument("--output",  default="corpus/toc",
                        help="Output dir (default: corpus/toc)")
    parser.add_argument("--report",  action="store_true",
                        help="Only regenerate + print the structure report")
    parser.add_argument("--limit",   type=int, default=0,
                        help="Process only first N PDFs")
    parser.add_argument("--force",   action="store_true",
                        help="Overwrite existing JSON files")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Seconds before a single PDF is killed (default: 30)")
    parser.add_argument("--workers", type=int, default=multiprocessing.cpu_count(),
                        help="Parallel worker processes (default: CPU count)")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Single PDF mode (no timeout — interactive) ───────────────────
    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            sys.exit(f"[ERROR] Not found: {pdf_path}")
        if _is_sidecar(pdf_path):
            sys.exit(f"[ERROR] Sidecar file, not a real PDF: {pdf_path}")
        result = extract_toc(pdf_path)
        out_path = out_dir / f"{pdf_path.stem}.toc.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"✓ {pdf_path.name}  →  {out_path}")
        print(f"  TOC entries:  {len(result['toc'])}")
        print(f"  Body starts:  page {result['body_starts_page']}")
        print(f"  Boilerplate:  {result['boilerplate']}")
        top = [e for e in result["toc"] if re.match(r"^\d+$", e["num"]) or
               re.match(r"^Annex\s+[A-Z]$", e["num"], re.I)]
        if top:
            print("  Top-level sections:")
            for e in top:
                print(f"    {e['num']:8}  {e['title']}  → p.{e['page']}")
        return

    # ── Batch mode ──────────────────────────────────────────────────
    pdfs = _find_pdfs(Path(args.input))
    if args.limit:
        pdfs = pdfs[:args.limit]

    if not pdfs:
        sys.exit(f"[ERROR] No PDFs found in {args.input}")

    workers = min(args.workers, len(pdfs))
    print(f"[INFO] Processing {len(pdfs)} PDFs → {out_dir}/  "
          f"(timeout={args.timeout}s/PDF, workers={workers})")

    # Separate already-cached from those that need processing
    todo: list[Path] = []
    cached_tocs: list[dict] = []
    for pdf_path in pdfs:
        out_path = out_dir / f"{pdf_path.stem}.toc.json"
        if not args.force and out_path.exists():
            try:
                cached_tocs.append(json.loads(out_path.read_text()))
                continue
            except Exception:
                pass
        todo.append(pdf_path)

    skipped = len(cached_tocs)
    all_tocs: list[dict] = list(cached_tocs)
    done = errors = 0

    def _process_one(pdf_path: Path) -> tuple[Path, dict | Exception]:
        try:
            return pdf_path, extract_toc_with_timeout(pdf_path, args.timeout)
        except Exception as exc:
            return pdf_path, exc

    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, p): p for p in todo}
        for fut in as_completed(futures):
            pdf_path, result = fut.result()
            out_path = out_dir / f"{pdf_path.stem}.toc.json"
            if isinstance(result, Exception):
                print(f"  ✗ {pdf_path.name}: {result}", file=sys.stderr)
                errors += 1
            else:
                out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
                all_tocs.append(result)
                toc_n = len(result["toc"])
                body_p = result["body_starts_page"] or "?"
                print(f"  ✓ {pdf_path.name:50s}  TOC={toc_n:3d}  body=p.{body_p}")
                done += 1

    summary_path = out_dir / "_summary.json"
    summary_path.write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "count": len(all_tocs), "documents": all_tocs},
                   indent=2, ensure_ascii=False)
    )

    report = build_report(all_tocs)
    report_path = out_dir / "_structure-report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"\n[📊] Done: {done} processed | {skipped} cached | {errors} errors")
    print(f"  📄 TOC JSONs  → {out_dir}/")
    print(f"  📋 Summary    → {summary_path}")
    print(f"  📝 Report     → {report_path}")
    print(f"\n  Open report:  open {report_path}")


if __name__ == "__main__":
    main()
