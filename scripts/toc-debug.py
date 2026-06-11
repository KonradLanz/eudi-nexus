#!/usr/bin/env python3
"""
toc-debug.py  —  Dump raw pdfplumber text for a PDF to diagnose TOC failures.

Usage:
  python3 scripts/toc-debug.py <pdf>              # pages 1-8 (default)
  python3 scripts/toc-debug.py <pdf> --pages 3-6  # specific range
  python3 scripts/toc-debug.py <pdf> --chars      # also show char-level font/size info per line
  python3 scripts/toc-debug.py <pdf> --toc-only   # only print lines that look like TOC candidates

Helps answer: why does this PDF get TOC=0 when it clearly has a TOC?
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("pip install pdfplumber")

_SIDECAR_RE = re.compile(r"^\.")

def _page_text_raw(page) -> str:
    """Raw text, no collapsing."""
    return page.extract_text(x_tolerance=3, y_tolerance=3) or ""

def _page_text_collapsed(page) -> str:
    """Same collapsing as toc-extract.py uses."""
    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not re.search(r"[.\s]{4,}", line):
            line = re.sub(r" {3,}", "  ", line)
        if line:
            lines.append(line)
    return "\n".join(lines)

def _char_summary(page) -> list[str]:
    """Per-line summary: avg font size, bold ratio, first 80 chars."""
    chars = page.chars
    if not chars:
        return []
    # group by rounded y-mid
    lines: dict[int, list] = {}
    for ch in chars:
        y = int((ch["top"] + ch["bottom"]) / 2)
        lines.setdefault(y, []).append(ch)
    result = []
    for y in sorted(lines):
        chs = lines[y]
        text = "".join(c["text"] for c in sorted(chs, key=lambda c: c["x0"]))
        sizes = [c.get("size", 0) for c in chs if c.get("size")]
        avg = sum(sizes) / len(sizes) if sizes else 0
        bold = sum(1 for c in chs if "Bold" in (c.get("fontname") or ""))
        result.append(f"  [{avg:5.1f}pt bold={bold/len(chs)*100:3.0f}%]  {text[:100]}")
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="1-8", help="Page range, e.g. 3-7")
    ap.add_argument("--chars", action="store_true", help="Show char-level font info")
    ap.add_argument("--toc-only", action="store_true",
                    help="Only show lines with a trailing number (TOC candidates)")
    ap.add_argument("--raw", action="store_true", help="Show raw (uncollapsed) text")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        sys.exit(f"Not found: {pdf_path}")
    if _SIDECAR_RE.match(pdf_path.name):
        sys.exit(f"Sidecar file: {pdf_path}")

    # parse page range
    m = re.match(r"(\d+)(?:-(\d+))?", args.pages)
    p_from = int(m.group(1))
    p_to   = int(m.group(2)) if m.group(2) else p_from + 7

    # trailing-number heuristic: line ends with spaces + 1-4 digit number
    _TRAILING_NUM = re.compile(r"\s+\d{1,4}\s*$")
    # dot-leader + number
    _DOT_LEADER   = re.compile(r"[.\s]{3,}\d{1,4}\s*$")

    print(f"\n{'='*72}")
    print(f"PDF: {pdf_path.name}")
    print(f"Pages: {p_from}–{p_to}")
    print(f"{'='*72}\n")

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        print(f"Total pages: {total}\n")

        for page in pdf.pages:
            nr = page.page_number
            if nr < p_from or nr > min(p_to, total):
                continue

            print(f"{'─'*72}")
            print(f"PAGE {nr}")
            print(f"{'─'*72}")

            if args.chars:
                print("[CHAR MODE — font size + bold ratio per line]")
                for line in _char_summary(page):
                    print(line)
                print()
                continue

            raw   = _page_text_raw(page)
            coll  = _page_text_collapsed(page)
            text  = raw if args.raw else coll

            toc_candidates = 0
            for i, line in enumerate(text.splitlines(), 1):
                is_candidate = bool(_TRAILING_NUM.search(line))
                has_dots     = bool(_DOT_LEADER.search(line))
                if args.toc_only and not is_candidate:
                    continue
                marker = ""
                if has_dots:     marker = " ●●● [DOT-LEADER]"
                elif is_candidate: marker = " ○○  [TRAILING-NUM]"
                print(f"  {i:3d}: {line}{marker}")
                if is_candidate:
                    toc_candidates += 1

            print(f"  → {toc_candidates} trailing-number lines on this page")
            print()

    print("\nHint: if you see TRAILING-NUM but TOC=0, the regex threshold")
    print("      in toc-extract.py needs lowering (currently needs 4 lines/page).")
    print("      Run with --chars to see font sizes for heading detection.")

if __name__ == "__main__":
    main()
