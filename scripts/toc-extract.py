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

npm:
  "toc":        ".venv/bin/python3 scripts/toc-extract.py",
  "toc:report": ".venv/bin/python3 scripts/toc-extract.py --report",
"""
from __future__ import annotations

import argparse
import json
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
# Regexes
# ──────────────────────────────────────────────────────────────────────────────

# TOC entry: "5.3  General requirements ......... 42"
_TOC_LINE_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)*\d+\.?)\s{1,6}(?P<title>[^\n]{2,80?}?)"
    r"[\.\s]{3,}\s*(?P<page>\d{1,4})\s*$"
)

# Looser TOC line (no dots, just section + title + trailing number)
_TOC_LOOSE_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)*\d+\.?)\s{2,}(?P<title>.{2,60}?)\s{2,}(?P<page>\d{1,4})\s*$"
)

# Annex entry in TOC: "Annex A (normative):  ... 55"
_TOC_ANNEX_RE = re.compile(
    r"^Annex\s+(?P<letter>[A-Z])\s*\((?P<type>normative|informative)\)[:\s]*"
    r"(?P<title>.{0,80}?)[\.\s]{0,20}\s*(?P<page>\d{1,4})\s*$",
    re.IGNORECASE,
)

# Cover page fields
_REF_RE     = re.compile(r"^Reference\s*$", re.MULTILINE)
_KW_RE      = re.compile(r"^Keywords\s*$", re.MULTILINE)
_DATE_RE    = re.compile(r"\b(January|February|March|April|May|June|July|August|"
                          r"September|October|November|December)\s+\d{4}\b")

# Known boilerplate section titles (appear after TOC, before real content)
_BOILERPLATE_HEADINGS = {
    "intellectual property rights",
    "foreword",
    "modal verbs terminology",
    "null",
    "introduction",  # sometimes intro is before scope — keep as marker
}

# The first "real" normative section is typically "1 Scope" or "1. Scope"
_SCOPE_RE = re.compile(r"^1\.?\s+Scope\b", re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ��─────────────────────────────────────────────────────────────────────────────

def _page_text(page) -> str:
    """Extract raw text from a pdfplumber page, collapsed whitespace per line."""
    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        # collapse internal runs of spaces > 2 (keep TOC dot leaders)
        if not re.search(r"[.\s]{4,}", line):
            line = re.sub(r" {3,}", "  ", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _is_toc_line(line: str) -> dict | None:
    """Return parsed TOC entry dict or None."""
    m = _TOC_LINE_RE.match(line)
    if m:
        return {"num": m.group("num").rstrip("."), "title": m.group("title").strip(), "page": int(m.group("page"))}
    m = _TOC_ANNEX_RE.match(line)
    if m:
        return {"num": f"Annex {m.group('letter')}", "title": m.group("title").strip(),
                "page": int(m.group("page")), "annex_type": m.group("type").lower()}
    m = _TOC_LOOSE_RE.match(line)
    if m:
        return {"num": m.group("num").rstrip("."), "title": m.group("title").strip(), "page": int(m.group("page"))}
    return None


def _looks_like_toc_page(lines: list[str], threshold: int = 4) -> bool:
    """Return True if the page has enough TOC-style lines."""
    return sum(1 for l in lines if _is_toc_line(l)) >= threshold


# ──────────────────────────────────────────────────────────────────────────────
# Per-PDF extractor
# ──────────────────────────────────────────────────────────────────────────────

def extract_toc(pdf_path: Path) -> dict:
    stem = pdf_path.stem
    result = {
        "stem":          stem,
        "pdf":           str(pdf_path),
        "title":         "",
        "reference":     stem,
        "keywords":      [],
        "date":          "",
        "toc":           [],       # list of {num, title, page}
        "boilerplate":   [],       # section titles found between TOC and body
        "body_starts_page": None,  # page where section "1 Scope" begins
        "total_pages":   0,
        "toc_pages":     [],
    }

    with pdfplumber.open(str(pdf_path)) as pdf:
        result["total_pages"] = len(pdf.pages)
        pages_text = []

        for page in pdf.pages:
            pages_text.append((page.page_number, _page_text(page)))

        # ── Cover page (page 1) ─────────────────────────────────────
        if pages_text:
            cover_lines = pages_text[0][1].splitlines()
            # Title: first non-empty line that's not "ETSI" or a spec number
            for line in cover_lines:
                if line and not re.match(r"^(ETSI|Draft|Final|V\d|EN\s|TS\s|TR\s)", line):
                    result["title"] = line
                    break
            # Date
            cover_text = pages_text[0][1]
            dm = _DATE_RE.search(cover_text)
            if dm:
                result["date"] = dm.group(0)

        # ── Find TOC pages ──────────────────────────────────────────
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
                # TOC ended — one grace page (some TOCs end mid-page)
                # check if any entries on this page
                grace = [_is_toc_line(l) for l in lines]
                grace_entries = [e for e in grace if e]
                if grace_entries:
                    toc_page_nums.append(page_nr)
                    toc_entries.extend(grace_entries)
                else:
                    break  # TOC is done

        result["toc"] = toc_entries
        result["toc_pages"] = toc_page_nums

        # ── Scan post-TOC pages for boilerplate + body start ────────
        post_toc_start = max(toc_page_nums) + 1 if toc_page_nums else 4
        boilerplate_found: list[str] = []

        for page_nr, text in pages_text:
            if page_nr < post_toc_start:
                continue
            lines = text.splitlines()
            for line in lines:
                # Check for known boilerplate headings
                lower = line.lower().strip()
                if lower in _BOILERPLATE_HEADINGS:
                    if lower not in [b.lower() for b in boilerplate_found]:
                        boilerplate_found.append(line.strip())
                # Check for "1 Scope" — body starts here
                if _SCOPE_RE.match(line.strip()):
                    result["body_starts_page"] = page_nr
                    break
            if result["body_starts_page"]:
                break

        result["boilerplate"] = boilerplate_found

    return result


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

    # ── Top-level section inventory across all docs ─────────────────
    top_sections: dict[str, int] = defaultdict(int)
    for doc in all_tocs:
        for entry in doc["toc"]:
            num = entry["num"]
            # Only top-level: single digit or "Annex X"
            if re.match(r"^\d+$", num) or re.match(r"^Annex\s+[A-Z]$", num, re.I):
                top_sections[entry["title"]] += 1

    lines += ["## Top-level sections (frequency across all documents)", ""]
    for title, count in sorted(top_sections.items(), key=lambda x: -x[1])[:40]:
        lines.append(f"  {count:4d}×  {title}")
    lines.append("")

    # ── Boilerplate sections found ──────────────────────────────────
    bp_counts: dict[str, int] = defaultdict(int)
    for doc in all_tocs:
        for bp in doc["boilerplate"]:
            bp_counts[bp] += 1

    lines += ["## Boilerplate sections between TOC and body", ""]
    for title, count in sorted(bp_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {count:4d}×  {title}")
    lines.append("")

    # ── Body start page distribution ───────────────────────────────
    starts = [d["body_starts_page"] for d in all_tocs if d["body_starts_page"]]
    if starts:
        lines += ["## Body start page distribution", ""]
        from collections import Counter
        for page, count in sorted(Counter(starts).items()):
            lines.append(f"  page {page:3d}:  {count:3d} documents")
        lines.append("")

    # ── Per-document TOC summary ────────────────────────────────────
    lines += ["## Per-document TOC (top-level only)", ""]
    for doc in sorted(all_tocs, key=lambda d: d["stem"]):
        top = [e for e in doc["toc"] if re.match(r"^\d+$", e["num"]) or
               re.match(r"^Annex\s+[A-Z]$", e["num"], re.I)]
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
    return sorted(root.rglob("*.pdf"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract TOC structure from ETSI PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pdf",    help="Single PDF to process")
    parser.add_argument("--input",  default="downloads/specs",
                        help="Root dir with PDFs (default: downloads/specs)")
    parser.add_argument("--output", default="corpus/toc",
                        help="Output dir (default: corpus/toc)")
    parser.add_argument("--report", action="store_true",
                        help="Print structure report to stdout and exit")
    parser.add_argument("--limit",  type=int, default=0,
                        help="Process only first N PDFs")
    parser.add_argument("--force",  action="store_true",
                        help="Overwrite existing JSON files")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Single PDF mode ─────────────────────────────────────────────
    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            sys.exit(f"[ERROR] Not found: {pdf_path}")
        result = extract_toc(pdf_path)
        out_path = out_dir / f"{pdf_path.stem}.toc.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"✓ {pdf_path.name}  →  {out_path}")
        print(f"  TOC entries: {len(result['toc'])}")
        print(f"  Body starts: page {result['body_starts_page']}")
        print(f"  Boilerplate: {result['boilerplate']}")
        return

    # ── Batch mode ──────────────────────────────────────────────────
    pdfs = _find_pdfs(Path(args.input))
    if args.limit:
        pdfs = pdfs[:args.limit]

    if not pdfs:
        sys.exit(f"[ERROR] No PDFs found in {args.input}")

    print(f"[INFO] Processing {len(pdfs)} PDFs → {out_dir}/")

    all_tocs: list[dict] = []
    done = skipped = errors = 0

    for pdf_path in pdfs:
        out_path = out_dir / f"{pdf_path.stem}.toc.json"
        if not args.force and out_path.exists():
            # Load existing for report
            try:
                all_tocs.append(json.loads(out_path.read_text()))
                skipped += 1
                continue
            except Exception:
                pass
        try:
            result = extract_toc(pdf_path)
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            all_tocs.append(result)
            toc_n = len(result["toc"])
            body_p = result["body_starts_page"] or "?"
            print(f"  ✓ {pdf_path.name:50s}  TOC={toc_n:3d}  body=p.{body_p}")
            done += 1
        except Exception as exc:
            print(f"  ✗ {pdf_path.name}: {exc}", file=sys.stderr)
            errors += 1

    # ── Summary JSON ────────────────────────────────────────────────
    summary_path = out_dir / "_summary.json"
    summary_path.write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "count": len(all_tocs), "documents": all_tocs},
                   indent=2, ensure_ascii=False)
    )

    # ── Markdown report ─────────────────────────────────────────────
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
