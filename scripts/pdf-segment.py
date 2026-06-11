#!/usr/bin/env python3
"""
pdf-segment.py  —  ETSI document structural segmentation pipeline

Primary path  : DOCX  (python-docx — structured paragraphs + real tables)
Fallback path : PDF   (pdfplumber — geometry-based, no auth needed)

Second-pass over corpus/specs/*.json (produced by pdf-ingest.py / docx-ingest.py).
For each page/paragraph the raw text is re-analysed to separate:

  HEADER   — repeating page header  (doc title, spec number, ETSI logo line)
  FOOTER   — page footer            (page number, date, copyright)
  TOC      — table of contents
  SECTION  — heading / clause title
  NORM     — normative body text    (RFC-2119: shall/shall not/…)
  INFORM   — informative text       (NOTE, Example, Annex (informative), …)
  TABLE    — table / figure caption
  OTHER    — cover page, boilerplate, blank

Output per spec
  corpus/specs/_segments/<stem>.segments.json   — machine-readable segments
  corpus/specs/_adoc/<stem>.adoc                — AsciiDoc with [[anchor]] backrefs

Style detection
  ETSI DOCX files use a consistent Word template across all specs:
    Heading 1–9  — numbered clauses and annexes      → SECTION
    ZA           — document title (cover page)        → OTHER
    ZB           — document type (European Standard)  → OTHER
    ZT           — scope line (cover page)            → OTHER
    NO           — NOTE paragraphs                    → INFORM
    EX           — EXAMPLE paragraphs                 → INFORM
    B1 / B1+     — requirement items                  → NORM
    BL           — normative body text                → NORM
    toc 1–9      — table of contents lines            → TOC
  detect_docx_heading_styles() scans a DOCX once and returns the set of
  heading-type style names actually present, so unknown templates are flagged
  automatically rather than silently misclassified.

Fair-use note:
  Local AI processing of standards documents for compliance research
  constitutes fair use under EU and international copyright law
  (Art. 5(3) InfoSoc Directive). No redistribution of document content.
  Auth credentials are never stored in this repository.

Usage:
  python3 scripts/pdf-segment.py                    # all corpus specs (DOCX preferred)
  python3 scripts/pdf-segment.py corpus/specs/ts_119403v020201p.json
  python3 scripts/pdf-segment.py --pdf path/to/doc.pdf
  python3 scripts/pdf-segment.py --docx path/to/doc.docx
  python3 scripts/pdf-segment.py --force             # overwrite existing
  python3 scripts/pdf-segment.py --profile etsi-contribution --pdf ESI.pdf
  python3 scripts/pdf-segment.py --scan-styles path/to/doc.docx  # style audit only

Dependencies:
  pip install pdfplumber python-docx
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
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import docx as docx_lib
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


# ─────────────────────────────────────────────────────────────────────────────
# Magic-byte file-type detection
# ─────────────────────────────────────────────────────────────────────────────

_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF"


def _sniff_kind(path: Path) -> str:
    """
    Read the first 4 bytes of *path* and return 'docx', 'pdf', or 'unknown'.

    ETSI sometimes serves DOCX files under a .pdf URL / filename.
    This check catches that mislabelling before pdfplumber tries to open the
    file and raises "No /Root object! - Is this really a PDF?".
    """
    try:
        header = path.read_bytes()[:4]
    except OSError:
        return "unknown"
    if header.startswith(_PDF_MAGIC):
        return "pdf"
    if header.startswith(_ZIP_MAGIC):
        return "docx"   # ZIP → treat as DOCX (Office Open XML)
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Profiles
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    name: str
    header_zone:        float = 0.10
    footer_zone:        float = 0.08
    heading_min_size:   float = 11.5
    heading_bold_ratio: float = 0.55
    toc_run_threshold:  int   = 4
    header_re:      list[str] = field(default_factory=list)
    footer_re:      list[str] = field(default_factory=list)
    boilerplate_re: list[str] = field(default_factory=list)


PROFILES: dict[str, Profile] = {
    "etsi-spec": Profile(
        name="etsi-spec",
        header_zone=0.10, footer_zone=0.09,
        heading_min_size=11.0, heading_bold_ratio=0.50,
        toc_run_threshold=4,
        header_re=[
            r"ETSI\s+(EN|TS|TR|GS|GR|EG)\s+\d",
            r"Draft\s+ETSI",
            r"^ETSI$",
        ],
        footer_re=[
            r"^\d+$",
            r"ETSI\s*$",
            r"\d{4}-\d{2}$",
            r"\u00a9\s*ETSI",
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
            r"In the present document",
        ],
    ),
    "etsi-contribution": Profile(
        name="etsi-contribution",
        header_zone=0.12, footer_zone=0.10,
        heading_min_size=11.0, heading_bold_ratio=0.50,
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
        header_zone=0.06, footer_zone=0.06,
        heading_min_size=10.5, heading_bold_ratio=0.40,
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


# ─────────────────────────────────────────────────────────────────────────────
# Shared regexes
# ─────────────────────────────────────────────────────────────────────────────

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
    r"^.{2,60}[\.\ ]{4,}\d{1,4}\s*$",
    re.MULTILINE,
)
_SECTION_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$",
    re.MULTILINE,
)
_BOILERPLATE_RE = re.compile(
    r"Intellectual Property|Essential patents|Important Notice"
    r"|Modal verbs terminology|Foreword",
    re.IGNORECASE,
)

SEGMENT_TYPES = (
    "HEADER", "FOOTER", "TOC", "SECTION",
    "NORM", "INFORM", "TABLE", "OTHER",
)

# ── ETSI Word template — known style → segment type mapping ──────────────────
# Derived empirically from all 15 DOCX in downloads/specs/EN/:
#   15x Heading 1–2, 11x Heading 3, 5x Heading 4, 2x Heading 5,
#   11x Heading 8, 2x Heading 9  → SECTION
#   13x ZA (doc title), 11x ZB (doc type), 14x ZT (scope) → OTHER (cover)
#   NO (notes), EX (examples) → INFORM
#   B1, B1+, BL (body normative) → NORM
#   toc 1–9 → TOC
_ETSI_STYLE_MAP: dict[str, str] = {
    # Cover page styles
    "za": "OTHER", "zb": "OTHER", "zt": "OTHER",
    "fp": "OTHER",   # front page metadata (Reference, Keywords…)
    # Informative
    "no": "INFORM",  # NOTE:
    "ex": "INFORM",  # EXAMPLE:
    "ew": "INFORM",  # definitions / glossary entries
    # Normative body
    "b1": "NORM", "b1+": "NORM", "b2": "NORM", "b2+": "NORM",
    "bl": "NORM",
}

# Styles NOT in this map but matching these patterns are flagged as unknown
_KNOWN_STYLE_PREFIXES = ("heading", "toc ", "normal", "default paragraph",
                          "za", "zb", "zt", "fp", "no", "ex", "ew",
                          "b1", "b1+", "b2", "b2+", "bl", "tt")


def find_normative_keywords(text: str) -> list[str]:
    return list({m.group(1).lower() for m in _RFC2119_RE.finditer(text)})


def _safe_stem(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic style detection  (scan once per DOCX, cache result)
# ─────────────────────────────────────────────────────────────────────────────

_style_cache: dict[str, set[str]] = {}  # path → set of lowercase style names


def detect_docx_heading_styles(docx_path: Path) -> tuple[set[str], set[str]]:
    """
    Scan all paragraph style names in *docx_path*.
    Returns (known_styles, unknown_styles) — both sets of lowercase names.

    known_styles  : styles the segmenter handles explicitly
    unknown_styles: styles not in _KNOWN_STYLE_PREFIXES (flag for AI review)

    Result is cached so repeated calls on the same path are free.
    """
    key = str(docx_path)
    if key in _style_cache:
        cached = _style_cache[key]
        known   = {s for s in cached if _is_known_style(s)}
        unknown = cached - known
        return known, unknown

    doc = docx_lib.Document(str(docx_path))
    all_styles: set[str] = set()
    for p in doc.paragraphs:
        if p.text.strip():
            all_styles.add((p.style.name or "").lower())

    _style_cache[key] = all_styles
    known   = {s for s in all_styles if _is_known_style(s)}
    unknown = all_styles - known
    return known, unknown


def _is_known_style(style_lower: str) -> bool:
    return any(style_lower.startswith(p) for p in _KNOWN_STYLE_PREFIXES)


def _warn_unknown_styles(unknown: set[str], docx_path: Path) -> None:
    """
    Print a warning when a DOCX contains style names we don't handle.
    In a future step these can be sent to a local AI model for classification.
    """
    if not unknown:
        return
    # Filter out noise (empty, single-char, purely numeric)
    flagged = {s for s in unknown if len(s) > 1 and not s.isdigit()}
    if not flagged:
        return
    print(
        f"   ⚠️  Unknown styles in {docx_path.name} "
        f"(may need AI classification): "
        f"{', '.join(sorted(flagged)[:8])}",
        file=sys.stderr,
    )
    # TODO: local AI fallback
    # If ollama / llama.cpp is available, classify each unknown style name
    # against a short prompt:
    #   "Given an ETSI standards Word style named '{style}' with sample text
    #    '{sample}', classify it as one of: SECTION NORM INFORM TABLE OTHER TOC"
    # For now we emit a warning so the user can inspect and extend _ETSI_STYLE_MAP.


# ─────────────────────────────────────────────────────────────────────────────
# DOCX segmentation  (primary path — python-docx)
# ─────────────────────────────────────────────────────────────────────────────

def _docx_para_is_bold(para) -> bool:
    """True if the majority of runs in the paragraph are bold."""
    runs = para.runs
    if not runs:
        return False
    bold_runs = sum(1 for r in runs if r.bold)
    return bold_runs / len(runs) >= 0.5


def _docx_para_type(para, profile: Profile) -> str:
    """
    Classify a python-docx Paragraph into a SEGMENT_TYPE.

    Priority order:
      1. Explicit ETSI style map (_ETSI_STYLE_MAP)
      2. Heading N  (case-insensitive — fixes SECTION=0 bug)
      3. toc N      (case-insensitive)
      4. Boilerplate text heuristic
      5. TOC dot-leader heuristic
      6. Informative text heuristic
      7. Bold numbered heading heuristic
      8. Figure/Table caption heuristic
      9. RFC-2119 normative keyword → NORM
     10. OTHER
    """
    style_raw  = para.style.name or ""
    style_low  = style_raw.lower()
    text       = para.text.strip()

    if not text:
        return "OTHER"

    # 1. Explicit ETSI template map (ZA/ZB/ZT/NO/EX/B1/BL …)
    mapped = _ETSI_STYLE_MAP.get(style_low)
    if mapped:
        return mapped

    # 2. Heading N  (case-insensitive — "Heading 1", "HEADING 1", …)
    if re.match(r"heading", style_low, re.IGNORECASE):
        return "SECTION"

    # 3. TOC styles
    if re.match(r"toc\s", style_low) or "contents" in style_low:
        return "TOC"

    # 4. Boilerplate cover text
    if _BOILERPLATE_RE.search(text):
        return "OTHER"

    # 5. TOC heuristic (dot leaders)
    if _TOC_LINE_RE.search(text):
        return "TOC"

    # 6. Informative
    if _INFORMATIVE_RE.search(text):
        return "INFORM"

    # 7. Bold short line that looks like a section number → SECTION
    if _docx_para_is_bold(para) and len(text) < 120:
        m = _SECTION_RE.match(text)
        if m:
            return "SECTION"

    # 8. Figure / table caption
    if re.match(r"^(Figure|Table|NOTE|EXAMPLE)\s+[\d\-A-Z]", text, re.IGNORECASE):
        return "TABLE"

    # 9. Normative keywords
    if find_normative_keywords(text):
        return "NORM"

    return "OTHER"


def segment_docx(
    docx_path: Path,
    profile: Profile,
    doc_stem: str | None = None,
) -> list[dict]:
    """
    Open a DOCX with python-docx and produce a flat list of segment dicts
    with the same schema used by segment_pdf().

    Tables are extracted with actual cell content (not just captions),
    making them far more useful than the PDF table extractor.
    """
    if not HAS_DOCX:
        raise RuntimeError(
            "python-docx not installed — run: pip install python-docx"
        )

    stem = doc_stem or _safe_stem(docx_path.stem)
    doc  = docx_lib.Document(str(docx_path))

    # Scan styles once and warn about unknown ones
    _, unknown_styles = detect_docx_heading_styles(docx_path)
    _warn_unknown_styles(unknown_styles, docx_path)

    segments: list[dict] = []
    seg_counters: dict[int, int] = {}
    current_section       = ""
    current_section_title = ""
    para_idx = 0  # synthetic paragraph index (no page numbers in DOCX)

    def _new_seg(seg_type: str, text: str, para_num: int) -> dict:
        seg_counters[para_num] = seg_counters.get(para_num, 0) + 1
        seg_id = f"{stem}_pa{para_num}_b{seg_counters[para_num]}"
        kw = find_normative_keywords(text) if seg_type in ("NORM", "SECTION", "OTHER") else []
        return {
            "id":                  seg_id,
            "type":               seg_type,
            "page":               para_num,
            "anchor":             f"#para={para_num}",
            "section":            current_section,        # captured AFTER update below
            "section_title":      current_section_title,
            "text":               text,
            "normative_keywords": kw,
            "profile":            profile.name,
        }

    body      = doc.element.body
    para_map  = {p._element: p for p in doc.paragraphs}
    table_map = {t._element: t for t in doc.tables}

    for child in body.iterchildren():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        # ── Paragraph ─────────────────────────────────────────────
        if tag == "p":
            para = para_map.get(child)
            if para is None:
                continue
            text     = para.text.strip()
            para_idx += 1
            if not text:
                continue

            seg_type = _docx_para_type(para, profile)

            # Update section state BEFORE building the segment dict
            # (fixes race condition: previous code set section AFTER _new_seg)
            if seg_type == "SECTION":
                m = _SECTION_RE.match(text)
                if m:
                    current_section       = m.group("num").rstrip(".")
                    current_section_title = m.group("title").strip()
                else:
                    # Heading style but no clause number (e.g. "Foreword")
                    current_section_title = text
                    # Keep current_section number unchanged so sub-clauses
                    # still inherit the parent section number.

            segments.append(_new_seg(seg_type, text, para_idx))

        # ── Table ──────────────────────────────────────────────────
        elif tag == "tbl":
            tbl = table_map.get(child)
            if tbl is None:
                continue
            para_idx += 1

            rows = []
            for row in tbl.rows:
                cells = [cell.text.strip() for cell in row.cells]
                # Deduplicate merged cells (python-docx repeats merged cell text)
                deduped: list[str] = []
                for c in cells:
                    if not deduped or c != deduped[-1]:
                        deduped.append(c)
                rows.append(deduped)

            if not rows:
                continue

            header    = rows[0]
            data_rows = rows[1:]
            flat_text = "\n".join(" | ".join(r) for r in rows)

            seg_counters[para_idx] = seg_counters.get(para_idx, 0) + 1
            seg_id = f"{stem}_pa{para_idx}_tbl{seg_counters[para_idx]}"

            segments.append({
                "id":                  seg_id,
                "type":               "TABLE",
                "page":               para_idx,
                "anchor":             f"#para={para_idx}",
                "section":            current_section,
                "section_title":      current_section_title,
                "text":               flat_text,
                "table_header":       header,
                "table_rows":         data_rows,
                "normative_keywords": find_normative_keywords(flat_text),
                "profile":            profile.name,
            })

    return segments


# ─────────────────────────────────────────────────────────────────────────────
# PDF segmentation  (fallback — pdfplumber)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TextBlock:
    text:          str
    y_top:         float
    y_bot:         float
    page_height:   float
    avg_font_size: float
    bold_ratio:    float

    @property
    def y_frac_top(self) -> float:
        return 1.0 - (self.y_bot / self.page_height)

    @property
    def y_frac_bot(self) -> float:
        return 1.0 - (self.y_top / self.page_height)


def _extract_blocks(page) -> list[TextBlock]:
    chars = page.chars
    if not chars:
        return []
    height = float(page.height)
    lines: dict[int, list[dict]] = {}
    for ch in chars:
        y_mid = int((ch["top"] + ch["bottom"]) / 2)
        lines.setdefault(y_mid, []).append(ch)
    sorted_ys = sorted(lines)
    blocks: list[TextBlock] = []
    current_chars: list[dict] = []
    current_ys:    list[int]  = []
    GAP_THRESHOLD = 8

    def _flush(ys: list[int], chs: list[dict]) -> None:
        if not chs:
            return
        text = "".join(c["text"] for c in sorted(chs, key=lambda c: (c["top"], c["x0"])))
        text = re.sub(r" {2,}", " ", text).strip()
        if not text:
            return
        sizes = [c.get("size", 0) for c in chs if c.get("size")]
        avg_size   = sum(sizes) / len(sizes) if sizes else 0.0
        bold_count = sum(
            1 for c in chs
            if "Bold" in (c.get("fontname") or "") or "bold" in (c.get("fontname") or "")
        )
        bold_ratio = bold_count / len(chs) if chs else 0.0
        y_top = float(min(c["top"]    for c in chs))
        y_bot = float(max(c["bottom"] for c in chs))
        blocks.append(TextBlock(text=text, y_top=y_top, y_bot=y_bot,
                                page_height=height, avg_font_size=avg_size,
                                bold_ratio=bold_ratio))

    for y in sorted_ys:
        if current_ys and (y - current_ys[-1]) > GAP_THRESHOLD:
            _flush(current_ys, current_chars)
            current_chars = []
            current_ys    = []
        current_chars.extend(lines[y])
        current_ys.append(y)
    _flush(current_ys, current_chars)
    return sorted(blocks, key=lambda b: b.y_top)


def classify_block(
    block: TextBlock,
    profile: Profile,
    page_nr: int,
    toc_mode: bool,
) -> str:
    text = block.text
    if block.y_frac_top < profile.header_zone:
        if any(re.search(r, text) for r in profile.header_re):
            return "HEADER"
        if page_nr > 1 and block.y_frac_bot < profile.header_zone * 1.5:
            return "HEADER"
    if block.y_frac_bot > (1.0 - profile.footer_zone):
        if any(re.search(r, text) for r in profile.footer_re):
            return "FOOTER"
        if page_nr > 1 and block.y_frac_top > (1.0 - profile.footer_zone * 1.5):
            return "FOOTER"
    if page_nr <= 4 and any(re.search(r, text) for r in profile.boilerplate_re):
        return "OTHER"
    toc_matches = len(_TOC_LINE_RE.findall(text))
    if toc_mode or toc_matches >= profile.toc_run_threshold:
        return "TOC"
    is_heading_font = (
        block.avg_font_size >= profile.heading_min_size
        and block.bold_ratio  >= profile.heading_bold_ratio
    )
    if is_heading_font:
        m = _SECTION_RE.match(text.strip())
        if m:
            return "SECTION"
    if re.match(r"^(Figure|Table|NOTE|EXAMPLE)\s+\d", text, re.IGNORECASE):
        return "TABLE"
    if _INFORMATIVE_RE.search(text):
        return "INFORM"
    if find_normative_keywords(text):
        return "NORM"
    return "OTHER"


def segment_pdf(
    pdf_path: Path,
    profile: Profile,
    doc_stem: str | None = None,
) -> list[dict]:
    if not HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber not installed — run: pip install pdfplumber")
    stem = doc_stem or _safe_stem(pdf_path.stem)
    segments: list[dict] = []
    seg_counters: dict[int, int] = {}
    current_section       = ""
    current_section_title = ""
    toc_page_count = 0
    MAX_TOC_PAGES  = 6

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_nr = page.page_number
            blocks  = _extract_blocks(page)
            toc_lines = sum(
                1 for b in blocks
                if len(_TOC_LINE_RE.findall(b.text)) >= 1
            )
            in_toc = toc_lines >= profile.toc_run_threshold
            if in_toc:
                toc_page_count += 1
            elif toc_page_count > 0 and toc_page_count < MAX_TOC_PAGES:
                toc_page_count = 0

            for block in blocks:
                if not block.text.strip():
                    continue
                seg_type = classify_block(
                    block, profile, page_nr,
                    toc_mode=(toc_page_count > 0 and toc_page_count < MAX_TOC_PAGES),
                )
                if seg_type == "SECTION":
                    m = _SECTION_RE.match(block.text.strip())
                    if m:
                        current_section       = m.group("num").rstrip(".")
                        current_section_title = m.group("title").strip()
                seg_counters[page_nr] = seg_counters.get(page_nr, 0) + 1
                seg_id = f"{stem}_p{page_nr}_b{seg_counters[page_nr]}"
                kw = find_normative_keywords(block.text) if seg_type in ("NORM", "SECTION", "OTHER") else []
                segments.append({
                    "id":                  seg_id,
                    "type":               seg_type,
                    "page":               page_nr,
                    "anchor":             f"#page={page_nr}",
                    "section":            current_section,
                    "section_title":      current_section_title,
                    "text":               block.text,
                    "normative_keywords": kw,
                    "profile":            profile.name,
                })
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Source file discovery
# ─────────────────────────────────────────────────────────────────────────────

DOWNLOAD_ROOTS = [
    Path("downloads/specs/TS"),
    Path("downloads/specs/EN"),
    Path("downloads/specs/TR"),
    Path("downloads/specs/SR"),
    Path("downloads/specs"),
]


def find_source_file(rec: dict, stem: str) -> tuple[Path | None, str]:
    """
    Resolve the best available source file for a corpus record.
    Returns (path, kind) where kind is 'docx' | 'pdf' | ''.

    Priority:
      1. source_docx field in corpus JSON (explicit, set by docx-ingest.py)
      2. source_pdf  field in corpus JSON  — magic-byte verified
         (ETSI sometimes serves DOCX under a .pdf URL; we detect that here
          so the caller never gets "No /Root object! - Is this really a PDF?")
      3. Auto-discover <stem>.docx in DOWNLOAD_ROOTS
      4. Auto-discover <stem>.pdf  in DOWNLOAD_ROOTS
    """
    # 1. Explicit DOCX
    docx_str = rec.get("source_docx", "")
    if docx_str:
        p = Path(docx_str)
        if p.is_file():
            return p, "docx"

    # 2. Explicit PDF — but verify magic bytes first
    pdf_str = rec.get("source_pdf", "")
    if pdf_str:
        p = Path(pdf_str)
        if p.is_file():
            real_kind = _sniff_kind(p)
            if real_kind == "unknown":
                real_kind = "pdf"
            return p, real_kind

    # 3 & 4. Auto-discover from norm stem (strip version suffix for glob)
    norm_base = re.sub(r"v\d{6}p$", "", stem)
    for root in DOWNLOAD_ROOTS:
        for suffix, kind in [(".docx", "docx"), (".pdf", "pdf")]:
            for p in sorted(root.glob(f"{norm_base}*{suffix}"), reverse=True):
                return p, kind  # highest version first

    return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# Shortname helper — e.g. "TS 119 615 v1.4.1"
# ─────────────────────────────────────────────────────────────────────────────

def _format_shortname(rec: dict) -> str:
    """
    Build a short human-readable label from corpus JSON fields, e.g.:
      TS 119 615 v1.4.1 — Electronic Registered Delivery Services
    Falls back gracefully when fields are absent.
    """
    norm    = rec.get("norm", "")          # e.g. "TS 119 615"
    version = rec.get("version", "")       # e.g. "1.4.1" or "V1.4.1"
    title   = rec.get("title", "")         # full English title
    # Also try common alternative keys
    if not title:
        title = rec.get("doc_title", rec.get("description", ""))
    if not norm:
        norm = rec.get("spec", rec.get("number", ""))

    # Normalise version prefix
    ver_str = f" v{version.lstrip('Vv')}" if version else ""
    title_str = f" — {title[:70]}" if title else ""
    return f"{norm}{ver_str}{title_str}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# AsciiDoc export
# ─────────────────────────────────────────────────────────────────────────────

_ADOC_SECTION_LEVEL = {1: "=", 2: "==", 3: "===", 4: "====", 5: "====="}


def _section_depth(section_num: str) -> int:
    if not section_num:
        return 1
    parts = section_num.strip(".").split(".")
    return min(len(parts), 5)


def segments_to_adoc(
    segments: list[dict],
    norm: str,
    version: str,
    source_path: Path | None = None,
) -> str:
    lines: list[str] = []
    src_label = source_path.name if source_path else "(source)"
    lines += [
        f"= {norm} {version}",
        ":doctype: article",
        ":source-highlighter: highlight.js",
        ":icons: font",
        ":toc: left",
        ":toclevels: 4",
        ":numbered:",
        "",
        "// Auto-generated by pdf-segment.py",
        f"// Source: {src_label}",
        "// Anchors link back to the original document",
        "",
    ]
    skip_types = {"HEADER", "FOOTER", "TOC", "OTHER"}
    for seg in segments:
        seg_type = seg["type"]
        if seg_type in skip_types:
            continue
        text    = seg["text"].strip()
        page_nr = seg["page"]
        anchor  = seg["id"]
        section = seg["section"]

        if seg_type == "SECTION":
            depth  = _section_depth(section)
            prefix = "=" * (depth + 1)
            anchor_label = f"{section} · p.{page_nr}" if section else f"p.{page_nr}"
            lines += ["", f"[[{anchor},{anchor_label}]]", f"{prefix} {text}", ""]

        elif seg_type in ("NORM", "INFORM"):
            role = "normative" if seg_type == "NORM" else "informative"
            kw   = seg.get("normative_keywords", [])
            kw_comment = f" // {', '.join(kw)}" if kw else ""
            lines += [
                f"[.{role}]#{kw_comment}",
                f"// {anchor} — para/page {page_nr}",
                text, "",
            ]

        elif seg_type == "TABLE":
            if "table_header" in seg:
                header = seg["table_header"]
                rows   = seg["table_rows"]
                lines += ["", f"// {anchor} — para {page_nr}"]
                lines += ["|===", " | ".join(f"{c}" for c in header)]
                for row in rows:
                    lines.append(" | ".join(f"{c}" for c in row))
                lines += ["|===", ""]
            else:
                lines += ["", f"// {anchor} — page {page_nr}",
                          f'[caption="{text[:80]}"]', "----", text, "----", ""]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# File I/O
# ─────────────────────────────────────────────────────────────────────────────

def _write_segments(
    segments: list[dict],
    norm: str,
    version: str,
    stem: str,
    corpus_root: Path,
    source_path: Path | None,
    source_kind: str = "pdf",
) -> tuple[Path, Path]:
    seg_dir  = corpus_root / "specs" / "_segments"
    adoc_dir = corpus_root / "specs" / "_adoc"
    seg_dir.mkdir(parents=True, exist_ok=True)
    adoc_dir.mkdir(parents=True, exist_ok=True)

    seg_path  = seg_dir  / f"{stem}.segments.json"
    adoc_path = adoc_dir / f"{stem}.adoc"

    meta = {
        "norm":         norm,
        "version":      version,
        "source_kind":  source_kind,
        f"source_{source_kind}": str(source_path) if source_path else None,
        "segmented_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "segment_count": len(segments),
        "type_counts": {
            t: sum(1 for s in segments if s["type"] == t)
            for t in SEGMENT_TYPES
        },
        "segments": segments,
    }
    seg_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    adoc_content = segments_to_adoc(segments, norm, version, source_path)
    adoc_path.write_text(adoc_content, encoding="utf-8")
    return seg_path, adoc_path


def iter_corpus_jsons(corpus_root: Path) -> Iterator[Path]:
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
) -> str:
    """
    Process a single corpus JSON.
    Tries DOCX first (richer table extraction), falls back to PDF.

    Returns one of:
      'processed'  — segmentation ran and output was written
      'skipped'    — already segmented (idempotent, --force not set)
      'nosource'   — no source file found
      'error'      — segmentation failed
    """
    try:
        rec = json.loads(corpus_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Cannot read {corpus_json}: {exc}", file=sys.stderr)
        return "error"

    norm      = rec.get("norm",    corpus_json.stem)
    version   = rec.get("version", "")
    stem      = _safe_stem(corpus_json.stem)
    shortname = _format_shortname(rec)

    seg_path = corpus_root / "specs" / "_segments" / f"{stem}.segments.json"
    if not force and seg_path.exists():
        if verbose:
            print(f"[⏭️]  {corpus_json.name}  ({shortname})  — already segmented")
        return "skipped"

    source_path, source_kind = find_source_file(rec, stem)
    if source_path is None:
        if verbose:
            print(f"[⚠️]  {corpus_json.name}  ({shortname})  — no source file found")
        return "nosource"

    if verbose:
        icon = "📄" if source_kind == "docx" else "📋"
        print(f"[🔄]  {corpus_json.name}  ({shortname})  [{icon} {source_kind.upper()}]")

    try:
        if source_kind == "docx":
            segments = segment_docx(source_path, profile, doc_stem=stem)
        else:
            segments = segment_pdf(source_path, profile, doc_stem=stem)
    except Exception as exc:
        print(f"[ERROR] {corpus_json.name}: segmentation failed: {exc}", file=sys.stderr)
        return "error"

    seg_path, adoc_path = _write_segments(
        segments, norm, version, stem, corpus_root, source_path, source_kind
    )

    if verbose:
        counts = {t: sum(1 for s in segments if s["type"] == t) for t in SEGMENT_TYPES}
        print(
            f"   ✓  {len(segments)} blocks  |  "
            f"NORM={counts.get('NORM', 0)}  "
            f"SECTION={counts.get('SECTION', 0)}  "
            f"TABLE={counts.get('TABLE', 0)}  "
            f"INFORM={counts.get('INFORM', 0)}  "
            f"OTHER={counts.get('OTHER', 0)}"
        )
        print(f"   📄  {seg_path}")
        print(f"   📝  {adoc_path}")
    return "processed"


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETSI document → structural segments + AsciiDoc (DOCX preferred, PDF fallback)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/pdf-segment.py                          # all corpus specs
  python3 scripts/pdf-segment.py corpus/specs/ts_119403v020201p.json
  python3 scripts/pdf-segment.py --pdf downloads/specs/EN/en319401v020201p.pdf
  python3 scripts/pdf-segment.py --docx downloads/specs/EN/ESI-0019401v331v322.docx
  python3 scripts/pdf-segment.py --force                  # overwrite existing
  python3 scripts/pdf-segment.py --profile etsi-contribution --pdf ESI-0019478.pdf
  python3 scripts/pdf-segment.py --stats                  # summary only
  python3 scripts/pdf-segment.py --scan-styles downloads/specs/EN/ESI-0019401v331v322.docx
""",
    )
    parser.add_argument(
        "inputs", nargs="*",
        help="corpus/*.json files. If empty: all corpus/specs/*.json",
    )
    parser.add_argument(
        "--pdf", metavar="PDF_PATH",
        help="Process a raw PDF directly (bypasses corpus JSON)",
    )
    parser.add_argument(
        "--docx", metavar="DOCX_PATH",
        help="Process a raw DOCX directly (bypasses corpus JSON)",
    )
    parser.add_argument(
        "--scan-styles", metavar="DOCX_PATH",
        help="Print all paragraph style names found in a DOCX (style audit, no segmentation)",
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
    parser.add_argument("--force",  "-f", action="store_true", help="Overwrite existing")
    parser.add_argument("--quiet",  "-q", action="store_true", help="Suppress per-block output")
    parser.add_argument("--stats",        action="store_true", help="Print type statistics")
    args = parser.parse_args()

    if not HAS_PDFPLUMBER and not HAS_DOCX:
        sys.exit("[ERROR] Neither pdfplumber nor python-docx installed.\n"
                 "        Run: pip install pdfplumber python-docx")

    profile     = PROFILES[args.profile]
    corpus_root = Path(args.corpus)
    verbose     = not args.quiet

    # ── Style audit mode ──────────────────────────────────────────
    if args.scan_styles:
        import collections
        p = Path(args.scan_styles)
        if not p.is_file():
            sys.exit(f"[ERROR] File not found: {p}")
        doc = docx_lib.Document(str(p))
        styles: collections.Counter[str] = collections.Counter()
        samples: dict[str, str] = {}
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                sn = para.style.name or ""
                styles[sn] += 1
                if sn not in samples:
                    samples[sn] = t[:70]
        print(f"\nStyles in {p.name}:")
        for name, n in sorted(styles.items()):
            known_tag = "" if _is_known_style(name.lower()) else "  ← UNKNOWN"
            mapped    = _ETSI_STYLE_MAP.get(name.lower(), "")
            map_tag   = f"  → {mapped}" if mapped else ""
            print(f"  {n:4}x  {name:<30}{map_tag}{known_tag}")
            print(f"         {samples[name]!r}")
        return

    # ── Direct DOCX mode ──────────────────────────────────────────
    if args.docx:
        docx_path = Path(args.docx)
        if not docx_path.is_file():
            sys.exit(f"[ERROR] DOCX not found: {docx_path}")
        stem = _safe_stem(docx_path.stem)
        print(f"[🔄]  {docx_path.name}  (profile: {profile.name})  [📄 DOCX]")
        segments = segment_docx(docx_path, profile, doc_stem=stem)
        seg_path, adoc_path = _write_segments(
            segments, docx_path.stem, "", stem, corpus_root, docx_path, "docx"
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

    # ── Direct PDF mode ───────────────────────────────────────────
    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            sys.exit(f"[ERROR] PDF not found: {pdf_path}")
        stem = _safe_stem(pdf_path.stem)
        print(f"[🔄]  {pdf_path.name}  (profile: {profile.name})  [📋 PDF]")
        segments = segment_pdf(pdf_path, profile, doc_stem=stem)
        seg_path, adoc_path = _write_segments(
            segments, pdf_path.stem, "", stem, corpus_root, pdf_path, "pdf"
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
        docx_ok = "✅" if HAS_DOCX       else "❌ (pip install python-docx)"
        pdf_ok  = "✅" if HAS_PDFPLUMBER else "❌ (pip install pdfplumber)"
        print(f"[INFO]  {len(corpus_jsons)} corpus JSON(s)  "
              f"(profile: {profile.name}, "
              f"{'--force' if args.force else 'idempotent'})")
        print(f"        DOCX support: {docx_ok}")
        print(f"        PDF  support: {pdf_ok}\n")

    done = skipped_done = skipped_nosource = errors = 0
    total_segments = 0
    type_totals: dict[str, int] = {t: 0 for t in SEGMENT_TYPES}
    docx_count = pdf_count = 0

    for cj in corpus_jsons:
        try:
            result = process_corpus_json(
                cj, profile, corpus_root, force=args.force, verbose=verbose
            )
            if result == "processed":
                done += 1
                stem     = _safe_stem(cj.stem)
                seg_path = corpus_root / "specs" / "_segments" / f"{stem}.segments.json"
                if seg_path.exists():
                    try:
                        data = json.loads(seg_path.read_text(encoding="utf-8"))
                        total_segments += data.get("segment_count", 0)
                        kind = data.get("source_kind", "pdf")
                        if kind == "docx":
                            docx_count += 1
                        else:
                            pdf_count += 1
                        for t in SEGMENT_TYPES:
                            type_totals[t] += data.get("type_counts", {}).get(t, 0)
                    except Exception:
                        pass
            elif result == "skipped":
                skipped_done += 1
            elif result == "nosource":
                skipped_nosource += 1
            else:
                errors += 1
        except Exception as exc:
            errors += 1
            print(f"[ERROR] {cj.name}: {exc}", file=sys.stderr)

    if verbose or args.stats:
        print(f"\n{'─'*60}")
        print(f"  Processed   : {done}  (DOCX: {docx_count}, PDF: {pdf_count})")
        print(f"  Already done: {skipped_done}")
        print(f"  No source   : {skipped_nosource}")
        print(f"  Errors      : {errors}")
        print(f"  Segments    : {total_segments:,}")
        if args.stats and total_segments:
            print(f"\n  Type breakdown:")
            for t in SEGMENT_TYPES:
                n = type_totals.get(t, 0)
                if n:
                    pct = n / total_segments * 100
                    print(f"    {t:12} {n:6,}  ({pct:5.1f}%)")
        print(f"{'─'*60}")


if __name__ == "__main__":
    main()
