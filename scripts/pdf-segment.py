#!/usr/bin/env python3
"""
pdf-segment.py  —  ETSI PDF structural segmentation pipeline

Second-pass over corpus/specs/*.json (produced by pdf-ingest.py).
For each page the raw text_clean is re-analysed with pdfplumber
to separate:

  HEADER   — repeating page header  (doc title, spec number, ETSI logo line)
  FOOTER   — page footer            (page number, date, copyright, confidentiality)
  TOC      — table of contents      (detected as runs of <dots> + number patterns)
  SECTION  — heading / clause title (bold / larger font in pdfplumber char metadata)
  NORM     — normative body text    (contains RFC-2119 keywords: shall/shall not/…)
  INFORM   — informative text       (NOTE, Example, Annex (informative), …)
  TABLE    — table / figure caption
  OTHER    — cover page, boilerplate, blank

Output per spec
  corpus/specs/_segments/<stem>.segments.json   — machine-readable segments
  corpus/specs/_adoc/<stem>.adoc                — AsciiDoc with [[anchor]] backrefs

Segment record:
  {
    "id":          "en319401_p12_s3",    # unique within doc
    "type":        "NORM",
    "page":        12,
    "anchor":      "#page=12",            # links back to published PDF
    "section":     "5.3",
    "section_title": "General requirements",
    "text":        "The TSP shall ...",
    "normative_keywords": ["shall"],
    "profile":     "etsi-spec"            # or "etsi-contribution"
  }

AsciiDoc anchor format:
  [[en319401_p12_s3,5.3 · p.12]]
  This links the reader back to page 12 of the original PDF.

Usage:
  # all corpus specs (idempotent)
  python3 scripts/pdf-segment.py

  # single corpus JSON
  python3 scripts/pdf-segment.py corpus/specs/ts_119403v020201p.json

  # direct PDF (re-ingests on the fly, does not write corpus JSON)
  python3 scripts/pdf-segment.py --pdf downloads/specs/EN/en319401v020201p.pdf

  # force overwrite
  python3 scripts/pdf-segment.py --force

  # contribution profile (for ETSI .docx/.pdf contributions from ingest_zip)
  python3 scripts/pdf-segment.py --profile etsi-contribution --pdf path/to/ESI-0019478.pdf

Profiles
  etsi-spec         (default)  normative ETSI EN/TS/TR PDFs from the portal
  etsi-contribution            contribution docs from ESI ZIP packages
  ietf-rfc                     IETF RFC plaintext / PDF

Dependencies:
  pip install pdfplumber
  (pdfplumber is already required by pdf-ingest.py)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import pdfplumber
except ImportError:
    sys.exit("[ERROR] pdfplumber not installed — run: pip install pdfplumber")


# ──────────────────────────────────────────────────────────────────────────────
# Profiles
# Each profile encodes the geometry and textual heuristics for a given
# document family. Extend by adding a new entry to PROFILES.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    name: str
    # Y-coordinate thresholds (fraction of page height: 0.0=top, 1.0=bottom)
    header_zone:  float = 0.10   # top N% of page = header candidate
    footer_zone:  float = 0.08   # bottom N% of page = footer candidate
    # Font-size threshold: chars larger than this are heading candidates
    heading_min_size: float = 11.5
    # Minimum fraction of chars on a line that must be bold for heading detection
    heading_bold_ratio: float = 0.55
    # How many TOC-style lines in a row trigger TOC detection
    toc_run_threshold: int = 4
    # Regular expressions for header/footer content (profile-specific)
    header_re: list[str] = field(default_factory=list)
    footer_re: list[str] = field(default_factory=list)
    # Regular expressions for boilerplate / cover-page blocks to discard
    boilerplate_re: list[str] = field(default_factory=list)


PROFILES: dict[str, Profile] = {
    "etsi-spec": Profile(
        name="etsi-spec",
        header_zone=0.10,
        footer_zone=0.09,
        heading_min_size=11.0,
        heading_bold_ratio=0.50,
        toc_run_threshold=4,
        header_re=[
            r"ETSI\s+(EN|TS|TR|GS|GR|EG)\s+\d",   # e.g. "ETSI EN 319 401"
            r"Draft\s+ETSI",
            r"^ETSI$",
        ],
        footer_re=[
            r"^\d+$",                               # bare page number
            r"ETSI\s*$",
            r"\d{4}-\d{2}$",                        # year-month at line end
            r"\u00a9\s*ETSI",                       # copyright
            r"Publicly Available",
            r"Restricted",
            r"Confidential",
        ],
        boilerplate_re=[
            r"^Reference\s*$",
            r"^Keywords\s*$",
            r"^ETSI\s+650 Route des Lucioles",
            r"Important Notice",
            r"Intellectual Property Rights",
            r"Essential patents",
            r"No guarantee can be given",
            r"Modal verbs terminology",
            r"In the present document",              # cover-page scope para
        ],
    ),
    "etsi-contribution": Profile(
        name="etsi-contribution",
        header_zone=0.12,
        footer_zone=0.10,
        heading_min_size=11.0,
        heading_bold_ratio=0.50,
        toc_run_threshold=3,
        header_re=[
            r"ETSI TC ESI",
            r"ESI\(\d{2}\)\d+",
            r"Source:\s+",
            r"^Meeting\b",
        ],
        footer_re=[
            r"^\d+$",
            r"\u00a9\s*ETSI",
            r"Publicly Available",
            r"Confidential",
        ],
        boilerplate_re=[
            r"The present document has been",
        ],
    ),
    "ietf-rfc": Profile(
        name="ietf-rfc",
        header_zone=0.06,
        footer_zone=0.06,
        heading_min_size=10.5,
        heading_bold_ratio=0.40,
        toc_run_threshold=4,
        header_re=[
            r"^RFC\s+\d+",
            r"Internet Engineering Task Force",
            r"^\[?RFC\d+\]?",
        ],
        footer_re=[
            r"^\[Page \d+\]$",
            r"Internet Standards Track",
        ],
        boilerplate_re=[
            r"Status of This Memo",
            r"Copyright Notice",
            r"ISSN:",
        ],
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# RFC-2119 normative keyword detection
# ──────────────────────────────────────────────────────────────────────────────

_RFC2119_RE = re.compile(
    r"\b(shall\s+not|shall|should\s+not|should|must\s+not|must"
    r"|required|recommended|may\s+not|may|optional)\b",
    re.IGNORECASE,
)

_INFORMATIVE_RE = re.compile(
    r"^\s*(NOTE\b|EXAMPLE\b|Example\s+\d|\[i\.\d+\])"
    r"|Annex\s+\w+\s+\(informative\)"
    r"|\(informative\)",
    re.IGNORECASE | re.MULTILINE,
)

_TOC_LINE_RE = re.compile(
    r"^.{2,60}[\.\s]{4,}\d{1,4}\s*$",  # "5.3  Requirements ......... 42"
    re.MULTILINE,
)

_SECTION_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$",
    re.MULTILINE,
)


def find_normative_keywords(text: str) -> list[str]:
    return list({m.group(1).lower() for m in _RFC2119_RE.finditer(text)})


# ──────────────────────────────────────────────────────────────────────────────
# Page-level geometry analysis via pdfplumber
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TextBlock:
    """A logical text block extracted from a single PDF page."""
    text: str
    y_top: float      # top y-coordinate (PDF space: 0=bottom)
    y_bot: float      # bottom y-coordinate
    page_height: float
    avg_font_size: float
    bold_ratio: float   # fraction of chars that are bold

    @property
    def y_frac_top(self) -> float:
        """Fraction from page TOP (0=top, 1=bottom)."""
        return 1.0 - (self.y_bot / self.page_height)

    @property
    def y_frac_bot(self) -> float:
        return 1.0 - (self.y_top / self.page_height)


def _extract_blocks(page) -> list[TextBlock]:
    """
    Extract text blocks from a pdfplumber page using character-level metadata.
    Groups chars into lines by y-position, then lines into blocks by y-gap.
    Returns list of TextBlock sorted top-to-bottom.
    """
    chars = page.chars
    if not chars:
        return []

    height = float(page.height)

    # Group chars by rounded y-midpoint into lines
    lines: dict[int, list[dict]] = {}
    for ch in chars:
        y_mid = int((ch["top"] + ch["bottom"]) / 2)
        lines.setdefault(y_mid, []).append(ch)

    sorted_ys = sorted(lines)

    # Build TextBlocks by grouping lines with small vertical gaps
    blocks: list[TextBlock] = []
    current_chars: list[dict] = []
    current_ys: list[int] = []
    GAP_THRESHOLD = 8  # px gap between lines before starting a new block

    def _flush(ys: list[int], chs: list[dict]) -> None:
        if not chs:
            return
        text = "".join(c["text"] for c in sorted(chs, key=lambda c: (c["top"], c["x0"])))
        text = re.sub(r" {2,}", " ", text).strip()
        if not text:
            return
        sizes = [c.get("size", 0) for c in chs if c.get("size")]
        avg_size = sum(sizes) / len(sizes) if sizes else 0.0
        bold_count = sum(
            1 for c in chs
            if "Bold" in (c.get("fontname") or "") or "bold" in (c.get("fontname") or "")
        )
        bold_ratio = bold_count / len(chs) if chs else 0.0
        y_top = float(min(c["top"] for c in chs))
        y_bot = float(max(c["bottom"] for c in chs))
        blocks.append(TextBlock(
            text=text,
            y_top=y_top, y_bot=y_bot,
            page_height=height,
            avg_font_size=avg_size,
            bold_ratio=bold_ratio,
        ))

    for y in sorted_ys:
        if current_ys and (y - current_ys[-1]) > GAP_THRESHOLD:
            _flush(current_ys, current_chars)
            current_chars = []
            current_ys = []
        current_chars.extend(lines[y])
        current_ys.append(y)

    _flush(current_ys, current_chars)
    return sorted(blocks, key=lambda b: b.y_top)


# ──────────────────────────────────────────────────────────────────────────────
# Block classifier
# ──────────────────────────────────────────────────────────────────────────────

SEGMENT_TYPES = (
    "HEADER", "FOOTER", "TOC", "SECTION",
    "NORM", "INFORM", "TABLE", "OTHER",
)


def classify_block(
    block: TextBlock,
    profile: Profile,
    page_nr: int,
    toc_mode: bool,
) -> str:
    """
    Classify a single TextBlock into one of SEGMENT_TYPES.
    Returns the segment type string.
    """
    text = block.text

    # ── Geometry: header / footer zones ─────────────────────────────
    if block.y_frac_top < profile.header_zone:
        # Check content: does it match a known header pattern?
        if any(re.search(r, text) for r in profile.header_re):
            return "HEADER"
        # Even without explicit match, very top zone is header on page > 1
        if page_nr > 1 and block.y_frac_bot < profile.header_zone * 1.5:
            return "HEADER"

    if block.y_frac_bot > (1.0 - profile.footer_zone):
        if any(re.search(r, text) for r in profile.footer_re):
            return "FOOTER"
        if page_nr > 1 and block.y_frac_top > (1.0 - profile.footer_zone * 1.5):
            return "FOOTER"

    # ── Boilerplate / cover-page ────────────────────────────────
    if page_nr <= 4 and any(re.search(r, text) for r in profile.boilerplate_re):
        return "OTHER"

    # ── TOC detection ───────────────────────────────────────────
    toc_matches = len(_TOC_LINE_RE.findall(text))
    if toc_mode or toc_matches >= profile.toc_run_threshold:
        return "TOC"

    # ── Section heading ──────────────────────────────────────────
    is_heading_font = (
        block.avg_font_size >= profile.heading_min_size
        and block.bold_ratio >= profile.heading_bold_ratio
    )
    if is_heading_font:
        # Must also look like a section number + title
        m = _SECTION_RE.match(text.strip())
        if m:
            return "SECTION"

    # ── Figure / table caption ────────────────────────────────────
    if re.match(r"^(Figure|Table|NOTE|EXAMPLE)\s+\d", text, re.IGNORECASE):
        return "TABLE"

    # ── Informative ──────────────────────────────────────────────
    if _INFORMATIVE_RE.search(text):
        return "INFORM"

    # ── Normative ────────────────────────────────────────────────
    kw = find_normative_keywords(text)
    if kw:
        return "NORM"

    return "OTHER"


# ──────────────────────────────────────────────────────────────────────────────
# Core segmentation
# ──────────────────────────────────────────────────────────────────────────────

def _safe_stem(text: str) -> str:
    """Make a safe lowercase slug from norm string."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def segment_pdf(
    pdf_path: Path,
    profile: Profile,
    doc_stem: str | None = None,
) -> list[dict]:
    """
    Open a PDF with pdfplumber, extract and classify all blocks.
    Returns a flat list of segment dicts.
    """
    stem = doc_stem or _safe_stem(pdf_path.stem)
    segments: list[dict] = []
    seg_counters: dict[int, int] = {}  # page -> block counter

    current_section = ""
    current_section_title = ""
    toc_page_count = 0
    MAX_TOC_PAGES = 6

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_nr = page.page_number
            blocks  = _extract_blocks(page)

            # TOC heuristic: if more than N consecutive TOC lines on this page
            toc_lines = sum(
                1 for b in blocks
                if len(_TOC_LINE_RE.findall(b.text)) >= 1
            )
            in_toc = toc_lines >= profile.toc_run_threshold
            if in_toc:
                toc_page_count += 1
            elif toc_page_count > 0 and toc_page_count < MAX_TOC_PAGES:
                toc_page_count = 0  # reset if TOC was short

            for block in blocks:
                if not block.text.strip():
                    continue

                seg_type = classify_block(
                    block, profile, page_nr,
                    toc_mode=(toc_page_count > 0 and toc_page_count < MAX_TOC_PAGES)
                )

                # Track current section from SECTION blocks
                if seg_type == "SECTION":
                    m = _SECTION_RE.match(block.text.strip())
                    if m:
                        current_section       = m.group("num").rstrip(".")
                        current_section_title = m.group("title").strip()

                # Unique segment ID
                seg_counters[page_nr] = seg_counters.get(page_nr, 0) + 1
                seg_id = f"{stem}_p{page_nr}_b{seg_counters[page_nr]}"

                kw = find_normative_keywords(block.text) if seg_type in ("NORM", "SECTION", "OTHER") else []

                segments.append({
                    "id":               seg_id,
                    "type":             seg_type,
                    "page":             page_nr,
                    "anchor":           f"#page={page_nr}",
                    "section":          current_section,
                    "section_title":    current_section_title,
                    "text":             block.text,
                    "normative_keywords": kw,
                    "profile":          profile.name,
                })

    return segments


# ──────────────────────────────────────────────────────────────────────────────
# AsciiDoc export
# ──────────────────────────────────────────────────────────────────────────────

_ADOC_SECTION_LEVEL = {
    1: "=",    # top-level clause
    2: "==",
    3: "===",
    4: "====",
    5: "=====",
}


def _section_depth(section_num: str) -> int:
    """Return depth of a section number like '5.3.1' → 3."""
    if not section_num:
        return 1
    parts = section_num.strip(".").split(".")
    return min(len(parts), 5)


def segments_to_adoc(
    segments: list[dict],
    norm: str,
    version: str,
    source_pdf_path: Path | None = None,
) -> str:
    """
    Convert a flat segment list to an AsciiDoc document.

    Each SECTION becomes an AsciiDoc heading with an [[anchor]] that encodes:
      - the segment ID  (machine-readable)
      - the section + page  (human-readable)
    so any cross-reference can be traced back to the exact page of the
    original published PDF.

    HEADER / FOOTER / TOC / OTHER blocks are suppressed.
    NORM and INFORM blocks become paragraphs.
    Normative paragraphs get a [.normative] role attribute.
    Informative paragraphs get a [.informative] role attribute.
    TABLE blocks get a [caption=] + listing block.
    """
    lines: list[str] = []
    pdf_label = source_pdf_path.name if source_pdf_path else "(PDF)"

    # Document header
    lines += [
        f"= {norm} {version}",
        f":doctype: article",
        f":source-highlighter: highlight.js",
        f":icons: font",
        f":toc: left",
        f":toclevels: 4",
        f":numbered:",
        f"",
        f"// Auto-generated by pdf-segment.py",
        f"// Source: {pdf_label}",
        f"// Anchors link back to the original PDF via #page=N",
        f"",
    ]

    skip_types = {"HEADER", "FOOTER", "TOC", "OTHER"}
    last_section = ""

    for seg in segments:
        seg_type = seg["type"]
        if seg_type in skip_types:
            continue

        text    = seg["text"].strip()
        page_nr = seg["page"]
        anchor  = seg["id"]
        section = seg["section"]
        sec_title = seg["section_title"]

        if seg_type == "SECTION":
            depth  = _section_depth(section)
            prefix = "=" * (depth + 1)   # h2+ (h1 is doc title)
            # Anchor encodes section + page for back-reference
            anchor_label = f"{section} · p.{page_nr}" if section else f"p.{page_nr}"
            lines += [
                f"",
                f"[[{anchor},{anchor_label}]]",
                f"{prefix} {text}",
                f"",
            ]
            last_section = section

        elif seg_type in ("NORM", "INFORM"):
            role = "normative" if seg_type == "NORM" else "informative"
            kw = seg.get("normative_keywords", [])
            kw_comment = f" // {', '.join(kw)}" if kw else ""
            # Inline anchor for paragraph-level back-reference
            lines += [
                f"[.{role}]#{kw_comment}",
                f"// {anchor} — page {page_nr}",
                text,
                f"",
            ]

        elif seg_type == "TABLE":
            lines += [
                f"",
                f"// {anchor} — page {page_nr}",
                f"[caption=\"{text[:80]}\"]",
                f"----",
                text,
                f"----",
                f"",
            ]

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# File I/O helpers
# ──────────────────────────────────────────────────────────────────────────────

def _write_segments(
    segments: list[dict],
    norm: str,
    version: str,
    stem: str,
    corpus_root: Path,
    source_pdf_path: Path | None,
) -> tuple[Path, Path]:
    """
    Write segments.json and .adoc file, return both paths.
    """
    seg_dir  = corpus_root / "specs" / "_segments"
    adoc_dir = corpus_root / "specs" / "_adoc"
    seg_dir.mkdir(parents=True, exist_ok=True)
    adoc_dir.mkdir(parents=True, exist_ok=True)

    seg_path  = seg_dir  / f"{stem}.segments.json"
    adoc_path = adoc_dir / f"{stem}.adoc"

    # Segments JSON
    meta = {
        "norm":        norm,
        "version":     version,
        "source_pdf":  str(source_pdf_path) if source_pdf_path else None,
        "segmented_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "segment_count": len(segments),
        "type_counts": {
            t: sum(1 for s in segments if s["type"] == t)
            for t in SEGMENT_TYPES
        },
        "segments": segments,
    }
    seg_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # AsciiDoc
    adoc_content = segments_to_adoc(segments, norm, version, source_pdf_path)
    adoc_path.write_text(adoc_content, encoding="utf-8")

    return seg_path, adoc_path


def iter_corpus_jsons(corpus_root: Path) -> Iterator[Path]:
    """Yield all corpus/specs/*.json files (not in subdirs)."""
    specs_dir = corpus_root / "specs"
    if not specs_dir.is_dir():
        return
    for p in sorted(specs_dir.glob("*.json")):
        yield p


def process_corpus_json(
    corpus_json: Path,
    profile: Profile,
    corpus_root: Path,
    force: bool = False,
    verbose: bool = True,
) -> bool:
    """
    Process a single corpus JSON: find the source PDF, segment it, write outputs.
    Returns True if processed, False if skipped.
    """
    try:
        rec = json.loads(corpus_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Cannot read {corpus_json}: {exc}", file=sys.stderr)
        return False

    norm    = rec.get("norm", corpus_json.stem)
    version = rec.get("version", "")
    stem    = _safe_stem(corpus_json.stem)

    # Check if already segmented
    seg_path = corpus_root / "specs" / "_segments" / f"{stem}.segments.json"
    if not force and seg_path.exists():
        if verbose:
            print(f"[⏭️]  {corpus_json.name}  — already segmented")
        return False

    # Find source PDF
    source_pdf_str = rec.get("source_pdf", "")
    pdf_path = Path(source_pdf_str) if source_pdf_str else None
    if not pdf_path or not pdf_path.is_file():
        if verbose:
            print(f"[⚠️]  {corpus_json.name}  — source PDF not found: {source_pdf_str}")
        return False

    if verbose:
        print(f"[🔄]  {corpus_json.name}  →  {stem}  ({norm} {version})")

    segments = segment_pdf(pdf_path, profile, doc_stem=stem)
    seg_path, adoc_path = _write_segments(
        segments, norm, version, stem, corpus_root, pdf_path
    )

    if verbose:
        counts = {t: sum(1 for s in segments if s["type"] == t) for t in SEGMENT_TYPES}
        norm_count  = counts.get("NORM", 0)
        total_count = len(segments)
        print(f"   ✓  {total_count} blocks  |  NORM={norm_count}  "
              f"SECTION={counts.get('SECTION',0)}  "
              f"HEADER={counts.get('HEADER',0)}  FOOTER={counts.get('FOOTER',0)}")
        print(f"   📄  {seg_path}")
        print(f"   📝  {adoc_path}")
    return True


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETSI PDF → structural segments + AsciiDoc with PDF back-references",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/pdf-segment.py                          # all corpus specs
  python3 scripts/pdf-segment.py corpus/specs/en_319401v020201p.json
  python3 scripts/pdf-segment.py --pdf downloads/specs/EN/en319401v020201p.pdf
  python3 scripts/pdf-segment.py --force                  # overwrite existing
  python3 scripts/pdf-segment.py --profile etsi-contribution --pdf ESI-0019478.pdf
  python3 scripts/pdf-segment.py --stats                  # summary only

Add to package.json:
  "segment":     "node --experimental-vm-modules scripts/pdf-segment.py"
Or run directly:
  python3 scripts/pdf-segment.py
""",
    )
    parser.add_argument(
        "inputs", nargs="*",
        help="corpus/*.json files or --pdf. If empty: all corpus/specs/*.json",
    )
    parser.add_argument(
        "--pdf", metavar="PDF_PATH",
        help="Process a raw PDF directly (bypasses corpus JSON lookup)",
    )
    parser.add_argument(
        "--profile", default="etsi-spec",
        choices=list(PROFILES),
        help="Segmentation profile (default: etsi-spec)",
    )
    parser.add_argument(
        "--corpus", default="corpus",
        help="Corpus root directory (default: corpus/)",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Overwrite existing segment files",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress per-block output",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print segment type statistics and exit",
    )
    args = parser.parse_args()

    profile     = PROFILES[args.profile]
    corpus_root = Path(args.corpus)
    verbose     = not args.quiet

    # ── Direct PDF mode ────────────────────────────────────────────
    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            sys.exit(f"[ERROR] PDF not found: {pdf_path}")
        stem = _safe_stem(pdf_path.stem)
        print(f"[🔄]  {pdf_path.name}  (profile: {profile.name})")
        segments = segment_pdf(pdf_path, profile, doc_stem=stem)
        seg_path, adoc_path = _write_segments(
            segments, pdf_path.stem, "", stem, corpus_root, pdf_path
        )
        if verbose:
            counts = {t: sum(1 for s in segments if s["type"] == t) for t in SEGMENT_TYPES}
            print(f"   ✓  {len(segments)} blocks total")
            for t in SEGMENT_TYPES:
                if counts.get(t, 0):
                    print(f"      {t:12} {counts[t]}")
            print(f"   📄  {seg_path}")
            print(f"   📝  {adoc_path}")
        return

    # ── Corpus JSON mode ──────────────────────────────────────────
    if args.inputs:
        corpus_jsons = [Path(p) for p in args.inputs]
    else:
        corpus_jsons = list(iter_corpus_jsons(corpus_root))
        if not corpus_jsons:
            sys.exit(
                f"[ERROR] No corpus JSON files found in {corpus_root / 'specs'}.\n"
                "        Run: npm run ingest"
            )

    if verbose:
        print(f"[INFO]  {len(corpus_jsons)} corpus JSON(s)  "
              f"(profile: {profile.name}, "
              f"{'--force' if args.force else 'idempotent'})\n")

    done = skipped = errors = 0
    total_segments = 0
    type_totals: dict[str, int] = {t: 0 for t in SEGMENT_TYPES}

    for cj in corpus_jsons:
        try:
            processed = process_corpus_json(
                cj, profile, corpus_root, force=args.force, verbose=verbose
            )
            if processed:
                done += 1
                # Read back stats from written file
                stem = _safe_stem(cj.stem)
                seg_path = corpus_root / "specs" / "_segments" / f"{stem}.segments.json"
                if seg_path.exists():
                    meta = json.loads(seg_path.read_text(encoding="utf-8"))
                    total_segments += meta.get("segment_count", 0)
                    for t, n in meta.get("type_counts", {}).items():
                        type_totals[t] = type_totals.get(t, 0) + n
            else:
                skipped += 1
        except Exception as exc:
            print(f"[ERROR] {cj.name}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n[📊]  Done: {done} processed | {skipped} skipped | {errors} errors")
    print(f"   Total segments: {total_segments}")
    if args.stats or verbose:
        for t in SEGMENT_TYPES:
            n = type_totals.get(t, 0)
            if n:
                print(f"   {t:12} {n:>6}")
    print(f"\n   📄 Segments → corpus/specs/_segments/")
    print(f"   📝 AsciiDoc  → corpus/specs/_adoc/")


if __name__ == "__main__":
    main()
