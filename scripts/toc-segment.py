#!/usr/bin/env python3
"""
toc-segment.py  —  TOC-guided, zero-loss ETSI PDF segmentation

Uses the pre-built TOC JSONs from corpus/toc/ as structural ground truth
to slice each PDF into precise per-section segments. Every character of
body text is preserved; header/footer strips are identified by geometry
but their raw text is retained in a separate `stripped` field so nothing
is permanently discarded.

Also extracts embedded raster images and renders each body page as a
high-res PNG so figures referenced in the text are available on the
filesystem.

Workflow:
  1. toc-extract.py       →  corpus/toc/<stem>.toc.json   (already done)
  2. toc-segment.py  (this)  →  corpus/segments/<stem>/
       meta.json            — document-level metadata + coverage stats
       sec_<num>.json       — one file per TOC entry (section)
  3. build-index.py          →  SQLite (only latest version per norm)

Segment JSON schema:
  {
    "id":          "ts_119512#7.3.1",
    "norm":        "ts_119512",
    "version":     "01.02.01",
    "num":         "7.3.1",
    "title":       "Subject registration",
    "page_from":   45,
    "page_to":     47,
    "depth":       3,
    "parent":      "ts_119512#7.3",
    "prev":        "ts_119512#7.2.4",
    "next":        "ts_119512#7.3.2",
    "type":        "norm",           // norm | inform | annex | toc | boilerplate
    "text":        "...",            // full body text, page-boundaries marked with \f
    "tables":      [...],            // extracted table rows [{"header":[...], "rows":[[...]]}
    "images":      [...],            // [{"path": "corpus/images/ts_119512/fig_5-1.png",
                                    //   "caption": "Figure 5-1: PKI Trust Model",
                                    //   "page": 45}]
    "normative_keywords": ["shall"],
    "cross_refs":  ["ts_119411#6.1"],
    "coverage":    {"chars_in_pdf": 1842, "chars_extracted": 1791, "ratio": 0.972}
  }

Image output:
  corpus/images/<stem>/page_<NNN>.png      — full page render (2x DPI)
  corpus/images/<stem>/<img_hash>.<ext>    — embedded raster image if extractable

Coverage check:
  Per segment: chars_extracted / chars_in_pdf ≥ WARNING_THRESHOLD (0.85)
  Segments below threshold are logged to corpus/segments/_coverage_warnings.json

Usage:
  python3 scripts/toc-segment.py                    # all PDFs with TOC JSON
  python3 scripts/toc-segment.py ts_119512          # single norm (stem)
  python3 scripts/toc-segment.py --force            # overwrite existing
  python3 scripts/toc-segment.py --no-images        # skip image extraction
  python3 scripts/toc-segment.py --stats            # coverage report only

Dependencies:
  pip install pdfplumber pymupdf   # pymupdf = fitz, for image extraction
"""
from __future__ import annotations

import argparse
import hashlib
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

try:
    import fitz  # pymupdf
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


# ──────────────────────────────────────────────────────────────────────────────
CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

CORPUS_ROOT      = Path("corpus")
TOC_DIR          = CORPUS_ROOT / "toc"
SEGMENTS_DIR     = CORPUS_ROOT / "segments"
IMAGES_DIR       = CORPUS_ROOT / "images"
DOWNLOAD_ROOTS   = [
    Path("downloads/specs/TS"),
    Path("downloads/specs/EN"),
    Path("downloads/specs/TR"),
    Path("downloads/specs/SR"),
    Path("downloads/specs"),
]

# Coverage: warn if extracted chars < this fraction of PDF chars
COVERAGE_WARNING = 0.85

# Image render resolution multiplier (2.0 ≈ 144 DPI for typical A4 PDFs)
IMAGE_SCALE = 2.0

# ETSI header/footer geometry thresholds (fraction of page height)
HEADER_ZONE = 0.10
FOOTER_ZONE = 0.09

# Patterns that identify header/footer lines (text is retained but flagged)
_HEADER_RE = re.compile(
    r"ETSI\s+(EN|TS|TR|GS|GR|EG)\s+\d|Draft\s+ETSI|^ETSI$",
    re.IGNORECASE | re.MULTILINE,
)
_FOOTER_RE = re.compile(
    r"^\d+$|ETSI\s*$|\d{4}-\d{2}$|\u00a9\s*ETSI|Publicly Available|Confidential",
    re.IGNORECASE | re.MULTILINE,
)

# RFC-2119 normative keyword detection
_RFC2119_RE = re.compile(
    r"\b(shall\s+not|shall|should\s+not|should|must\s+not|must"
    r"|required|recommended|may\s+not|may|optional)\b",
    re.IGNORECASE,
)

# Cross-reference detection: "clause 7.3.1", "see 4.2", "Annex B"
_XREF_RE = re.compile(
    r"\b(?:clause|section|annex)\s+([A-Z]?\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

# Figure caption detection
_FIGURE_RE = re.compile(
    r"(?:Figure|Fig\.?)\s+(?P<num>[\d\-A-Z]+)[:\s]+(?P<caption>[^\n]{5,120})",
    re.IGNORECASE,
)

# Section type classification from title / numbering
_ANNEX_RE   = re.compile(r"^Annex\s+[A-Z]", re.IGNORECASE)
_INFORM_RE  = re.compile(r"\(informative\)|^NOTE\b|^EXAMPLE\b", re.IGNORECASE)
_BOILER_RE  = re.compile(
    r"Intellectual Property|Essential patents|Important Notice"
    r"|Modal verbs terminology|Foreword|Introduction",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────────
VERSION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def parse_version(stem: str) -> tuple[int, int, int]:
    """Extract version tuple from ETSI filename stem.
    ts_119512v010201p  ->  (1, 2, 1)
    Returns (0,0,0) if not parseable.
    """
    m = re.search(r'v(\d{2})(\d{2})(\d{2})p', stem)
    return (int(m[1]), int(m[2]), int(m[3])) if m else (0, 0, 0)


def norm_id_from_stem(stem: str) -> str:
    """Strip version suffix: ts_119512v010201p -> ts_119512."""
    return re.sub(r'v\d{6}p$', '', stem)


# ──────────────────────────────────────────────────────────────────────────────
PDF PATH RESOLUTION
# ──────────────────────────────────────────────────────────────────────────────

def find_pdf(stem: str) -> Path | None:
    """Search known download directories for <stem>.pdf."""
    filename = stem + ".pdf"
    for root in DOWNLOAD_ROOTS:
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


# ──────────────────────────────────────────────────────────────────────────────
PAGE TEXT EXTRACTION (zero-loss)
# ──────────────────────────────────────────────────────────────────────────────

def _page_chars_count(page) -> int:
    """Total character count on a raw pdfplumber page (ground truth)."""
    return sum(1 for c in page.chars if c.get("text", "").strip())


def extract_page_text(page) -> dict:
    """
    Extract ALL text from a page with zero-loss approach:
    1. layout=True preserves reading order in multi-column layouts
    2. Tables are extracted separately and appended so no cell is lost
    3. Returns separate body / header_footer texts based on y-geometry

    Returns:
      {
        "body":   str   -- main content (header/footer stripped),
        "hf":     str   -- header+footer text (preserved, not discarded),
        "tables": list  -- [{"header": [...], "rows": [[...]]}, ...],
        "chars_total": int,
        "chars_body":  int,
      }
    """
    h = float(page.height)
    header_y_max = h * HEADER_ZONE        # below this y = header zone
    footer_y_min = h * (1 - FOOTER_ZONE)  # above this y = footer zone

    # Separate chars into body vs header/footer by y-position
    body_chars = []
    hf_chars   = []
    for ch in page.chars:
        if not ch.get("text", "").strip():
            continue
        y_mid = (ch["top"] + ch["bottom"]) / 2
        if y_mid < header_y_max or y_mid > footer_y_min:
            hf_chars.append(ch)
        else:
            body_chars.append(ch)

    # Crop to body zone for layout-aware extraction
    body_bbox = (0, header_y_max, page.width, footer_y_min)
    body_crop = page.within_bbox(body_bbox, relative=False)

    # layout=True preserves column reading order
    body_text = body_crop.extract_text(layout=True) or ""

    # Header/footer raw text (retained, never discarded)
    hf_crop = page.filter(lambda obj: obj.get("object_type") == "char" and (
        (obj.get("top", 0) + obj.get("bottom", 0)) / 2 < header_y_max
        or (obj.get("top", 0) + obj.get("bottom", 0)) / 2 > footer_y_min
    ))
    hf_text = (hf_crop.extract_text() or "") if hf_chars else ""

    # Tables — extract from body zone, row by row
    tables = []
    for tbl in body_crop.extract_tables():
        if not tbl:
            continue
        # First row is header if it looks like one (all cells non-empty)
        header_row = tbl[0] if tbl else []
        data_rows  = tbl[1:] if len(tbl) > 1 else tbl
        tables.append({
            "header": [cell or "" for cell in header_row],
            "rows":   [[cell or "" for cell in row] for row in data_rows],
        })
        # Append table text to body so it's counted in coverage
        for row in tbl:
            row_text = " | ".join(cell or "" for cell in row)
            body_text += "\n" + row_text

    return {
        "body":        body_text.strip(),
        "hf":          hf_text.strip(),
        "tables":      tables,
        "chars_total": len(page.chars),
        "chars_body":  len(body_chars),
    }


# ──────────────────────────────────────────────────────────────────────────────
IMAGE EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def extract_images_for_pdf(pdf_path: Path, stem: str, do_pages: bool = True) -> list[dict]:
    """
    Extract all embedded images and render body pages as PNG.
    Returns list of image metadata dicts.
    Requires pymupdf (fitz). If unavailable, returns empty list.
    """
    if not HAS_FITZ:
        return []

    out_dir = IMAGES_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    image_meta: list[dict] = []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        print(f"  [images] Cannot open {pdf_path.name}: {exc}", file=sys.stderr)
        return []

    for page_idx in range(len(doc)):
        page_nr = page_idx + 1
        page    = doc[page_idx]

        # 1. Render full page as PNG (captures vector figures too)
        if do_pages:
            mat = fitz.Matrix(IMAGE_SCALE, IMAGE_SCALE)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            page_img_path = out_dir / f"page_{page_nr:03d}.png"
            pix.save(str(page_img_path))
            image_meta.append({
                "type":    "page_render",
                "page":    page_nr,
                "path":    str(page_img_path),
                "caption": None,
            })

        # 2. Extract embedded raster images (logos, photos, embedded diagrams)
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image  = doc.extract_image(xref)
                img_bytes   = base_image["image"]
                ext         = base_image.get("ext", "png")
                # Deduplicate by content hash
                img_hash = hashlib.sha1(img_bytes).hexdigest()[:12]
                img_path = out_dir / f"{img_hash}.{ext}"
                if not img_path.exists():
                    img_path.write_bytes(img_bytes)
                image_meta.append({
                    "type":    "embedded",
                    "page":    page_nr,
                    "path":    str(img_path),
                    "caption": None,   # filled in later from text
                })
            except Exception:
                pass  # corrupted xref — skip silently

    doc.close()
    return image_meta


def _attach_figure_captions(images: list[dict], page_texts: dict[int, str]) -> None:
    """
    Find Figure captions in page text and attach them to the nearest image
    on the same page.
    Modifies images in-place.
    """
    for img in images:
        if img["caption"] is not None:
            continue
        page_text = page_texts.get(img["page"], "")
        m = _FIGURE_RE.search(page_text)
        if m:
            img["caption"] = f"Figure {m.group('num')}: {m.group('caption').strip()}"


# ──────────────────────────────────────────────────────────────────────────────
SECTION TYPE CLASSIFIER
# ──────────────────────────────────────────────────────────────────────────────

def classify_section(entry: dict) -> str:
    """
    Classify a TOC entry into: norm | inform | annex | toc | boilerplate
    Uses the section number and title from the TOC JSON.
    """
    num   = (entry.get("num")   or "").strip()
    title = (entry.get("title") or "").strip()
    combined = f"{num} {title}"

    if _BOILER_RE.search(combined):
        return "boilerplate"
    if _ANNEX_RE.search(combined):
        if _INFORM_RE.search(title):
            return "inform"
        return "annex"
    if _INFORM_RE.search(title):
        return "inform"
    # Pure digit section numbers are normative body
    if re.match(r'^\d', num):
        return "norm"
    return "norm"  # default


# ──────────────────────────────────────────────────────────────────────────────
CORE SEGMENTATION
# ──────────────────────────────────────────────────────────────────────────────

def segment_pdf_with_toc(
    pdf_path: Path,
    toc_data: dict,
    stem: str,
    extract_images: bool = True,
) -> tuple[list[dict], dict]:
    """
    Main segmentation function.

    Args:
        pdf_path:        Path to the source PDF.
        toc_data:        Parsed .toc.json dict from toc-extract.py.
        stem:            Filename stem (e.g. "ts_119512v010201p").
        extract_images:  Whether to extract page renders + embedded images.

    Returns:
        (segments, coverage_report)
    """
    norm_id   = norm_id_from_stem(stem)
    version_t = parse_version(stem)
    version   = ".".join(f"{v:02d}" for v in version_t)
    toc       = toc_data.get("toc", [])

    if not toc:
        return [], {"error": "empty TOC"}

    # ---- 1. Extract all page texts up front --------------------------------
    page_extracts: dict[int, dict] = {}   # page_nr → extract_page_text() result
    total_chars_pdf = 0
    total_chars_body = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        for page in pdf.pages:
            result = extract_page_text(page)
            page_extracts[page.page_number] = result
            total_chars_pdf  += result["chars_total"]
            total_chars_body += result["chars_body"]

    # ---- 2. Extract images (optional) --------------------------------------
    all_images: list[dict] = []
    if extract_images:
        all_images = extract_images_for_pdf(pdf_path, stem)
        page_texts_for_captions = {
            pg: ex["body"] for pg, ex in page_extracts.items()
        }
        _attach_figure_captions(all_images, page_texts_for_captions)

    # ---- 3. Build page-range map from TOC ----------------------------------
    # Each TOC entry: {num, title, page, depth, ...}
    # page_from = entry.page (1-based)
    # page_to   = next_entry.page - 1  (or total_pages for last entry)
    sorted_toc = sorted(toc, key=lambda e: (e.get("page", 0), e.get("num", "")))

    page_ranges: list[tuple[dict, int, int]] = []
    for i, entry in enumerate(sorted_toc):
        p_from = entry.get("page", 1)
        p_to   = (sorted_toc[i + 1]["page"] - 1
                  if i + 1 < len(sorted_toc)
                  else total_pages)
        p_to   = max(p_from, p_to)  # guard against inversion
        page_ranges.append((entry, p_from, p_to))

    # ---- 4. Build prev/next maps -------------------------------------------
    nums = [e.get("num", "") for e in sorted_toc]
    prev_map = {nums[i]: nums[i-1] if i > 0 else None
                for i in range(len(nums))}
    next_map = {nums[i]: nums[i+1] if i+1 < len(nums) else None
                for i in range(len(nums))}

    # Parent: strip last ".N" component
    def parent_num(num: str) -> str | None:
        parts = num.rsplit(".", 1)
        return parts[0] if len(parts) > 1 and parts[0] else None

    # ---- 5. Build segments -------------------------------------------------
    segments: list[dict] = []
    coverage_warnings: list[dict] = []

    for entry, p_from, p_to in page_ranges:
        num       = entry.get("num", "")
        title     = entry.get("title", "")
        depth     = entry.get("depth", len(num.split(".")))
        sec_type  = classify_section(entry)
        parent    = parent_num(num)
        seg_id    = f"{norm_id}#{num}" if num else f"{norm_id}#p{p_from}"

        # Concatenate body text for pages p_from..p_to
        # Page boundaries are marked with \f (form feed) so no text is lost
        page_texts  = []
        page_tables : list[dict] = []
        seg_chars_pdf  = 0
        seg_chars_body = 0

        for pg in range(p_from, p_to + 1):
            ex = page_extracts.get(pg)
            if ex is None:
                continue
            page_texts.append(ex["body"])
            page_tables.extend(ex["tables"])
            seg_chars_pdf  += ex["chars_total"]
            seg_chars_body += ex["chars_body"]

        # Join pages; \f marks page boundary so reader can reconstruct pagination
        body_text = "\f".join(t for t in page_texts if t)

        # Stitch page-boundary sentences: if text before \f doesn't end with
        # sentence-terminal punctuation, collapse the boundary
        body_text = re.sub(
            r'([^.!?:\n])\f([a-z])',
            r'\1 \2',
            body_text,
        )

        # Coverage check
        cov_ratio = (seg_chars_body / seg_chars_pdf) if seg_chars_pdf else 1.0
        if seg_chars_pdf > 0 and cov_ratio < COVERAGE_WARNING:
            coverage_warnings.append({
                "segment_id":    seg_id,
                "page_from":     p_from,
                "page_to":       p_to,
                "chars_pdf":     seg_chars_pdf,
                "chars_body":    seg_chars_body,
                "ratio":         round(cov_ratio, 3),
            })

        # Normative keywords
        norm_kw = list({m.group(1).lower()
                        for m in _RFC2119_RE.finditer(body_text)})

        # Cross-references to other sections / norms
        cross_refs = list({m.group(1) for m in _XREF_RE.finditer(body_text)})

        # Images belonging to this page range
        seg_images = [
            img for img in all_images
            if p_from <= img["page"] <= p_to
        ]

        segments.append({
            "id":                seg_id,
            "norm":              norm_id,
            "version":           version,
            "num":               num,
            "title":             title,
            "page_from":         p_from,
            "page_to":           p_to,
            "depth":             depth,
            "parent":            f"{norm_id}#{parent}" if parent else None,
            "prev":              f"{norm_id}#{prev_map[num]}" if prev_map.get(num) else None,
            "next":              f"{norm_id}#{next_map[num]}" if next_map.get(num) else None,
            "type":              sec_type,
            "text":              body_text,
            "tables":            page_tables,
            "images":            seg_images,
            "normative_keywords": norm_kw,
            "cross_refs":        cross_refs,
            "coverage": {
                "chars_in_pdf":    seg_chars_pdf,
                "chars_extracted": seg_chars_body,
                "ratio":           round(cov_ratio, 3),
            },
        })

    coverage_report = {
        "stem":              stem,
        "norm_id":           norm_id,
        "version":           version,
        "total_pages":       total_pages,
        "total_chars_pdf":   total_chars_pdf,
        "total_chars_body":  total_chars_body,
        "overall_ratio":     round(total_chars_body / total_chars_pdf, 3)
                             if total_chars_pdf else 0.0,
        "segment_count":     len(segments),
        "warnings":          coverage_warnings,
    }

    return segments, coverage_report


# ──────────────────────────────────────────────────────────────────────────────
FILE I/O
# ──────────────────────────────────────────────────────────────────────────────

def write_segments(stem: str, segments: list[dict], coverage: dict) -> Path:
    """
    Write output to corpus/segments/<stem>/
      meta.json        — document metadata + coverage
      sec_<safe_num>.json  — one file per segment
    Returns the segment directory.
    """
    out_dir = SEGMENTS_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # meta.json
    meta = {
        "stem":           stem,
        "norm_id":        coverage["norm_id"],
        "version":        coverage["version"],
        "segment_count":  len(segments),
        "segmented_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage":       coverage,
        "segment_ids":    [s["id"] for s in segments],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # One file per segment — safe filename from section num
    for seg in segments:
        safe_num = re.sub(r"[^a-z0-9]+", "_", seg["num"].lower()).strip("_")
        filename = f"sec_{safe_num}.json" if safe_num else f"sec_p{seg['page_from']}.json"
        (out_dir / filename).write_text(
            json.dumps(seg, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return out_dir


def iter_toc_jsons() -> Iterator[Path]:
    """Yield all *.toc.json files in corpus/toc/."""
    if not TOC_DIR.is_dir():
        return
    yield from sorted(TOC_DIR.glob("*.toc.json"))


# ──────────────────────────────────────────────────────────────────────────────
CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="TOC-guided zero-loss ETSI PDF segmentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/toc-segment.py                 # all PDFs with TOC JSON
  python3 scripts/toc-segment.py ts_119512       # single norm
  python3 scripts/toc-segment.py --force         # overwrite existing
  python3 scripts/toc-segment.py --no-images     # skip image extraction
  python3 scripts/toc-segment.py --stats         # coverage report only
""",
    )
    parser.add_argument(
        "stems", nargs="*",
        help="Norm stems to process (e.g. ts_119512). Default: all.",
    )
    parser.add_argument("--force",     "-f", action="store_true",
                        help="Overwrite existing segment directories")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip image extraction (faster)")
    parser.add_argument("--stats",     action="store_true",
                        help="Print coverage report after processing")
    parser.add_argument("--quiet",     "-q", action="store_true",
                        help="Suppress per-file output")
    args = parser.parse_args()

    if not HAS_FITZ and not args.no_images:
        print("[WARN] pymupdf not installed — image extraction disabled."
              " Install with: pip install pymupdf", file=sys.stderr)

    # Collect TOC JSONs to process
    if args.stems:
        toc_files = []
        for s in args.stems:
            # Accept bare norm stem (ts_119512) or full stem with version
            matches = list(TOC_DIR.glob(f"{s}*.toc.json"))
            if not matches:
                print(f"[WARN] No TOC JSON found for: {s}", file=sys.stderr)
            toc_files.extend(matches)
    else:
        toc_files = list(iter_toc_jsons())

    if not toc_files:
        sys.exit(f"[ERROR] No TOC JSONs found in {TOC_DIR}. Run: npm run toc")

    print(f"[INFO] Processing {len(toc_files)} PDF(s) → corpus/segments/")
    if not HAS_FITZ or args.no_images:
        print("       Image extraction: disabled")
    else:
        print("       Image extraction: enabled (page renders + embedded)")
    print()

    all_coverage: list[dict] = []
    done = skipped = errors = 0

    for toc_path in toc_files:
        # Derive stem from toc filename: ts_119512v010201p.toc.json → ts_119512v010201p
        stem = toc_path.name.replace(".toc.json", "")

        # Skip if already segmented (unless --force)
        seg_dir = SEGMENTS_DIR / stem
        if not args.force and (seg_dir / "meta.json").exists():
            if not args.quiet:
                print(f"  ⏭️  {stem}  (already segmented)")
            skipped += 1
            continue

        # Find PDF
        pdf_path = find_pdf(stem)
        if pdf_path is None:
            print(f"  ⚠️  {stem}  — PDF not found", file=sys.stderr)
            errors += 1
            continue

        # Load TOC JSON
        try:
            toc_data = json.loads(toc_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ❌  {stem}: cannot read TOC JSON: {exc}", file=sys.stderr)
            errors += 1
            continue

        # Segment
        try:
            segments, coverage = segment_pdf_with_toc(
                pdf_path, toc_data, stem,
                extract_images=(HAS_FITZ and not args.no_images),
            )
        except Exception as exc:
            print(f"  ❌  {stem}: segmentation error: {exc}", file=sys.stderr)
            errors += 1
            continue

        # Write
        try:
            out_dir = write_segments(stem, segments, coverage)
        except Exception as exc:
            print(f"  ❌  {stem}: write error: {exc}", file=sys.stderr)
            errors += 1
            continue

        done += 1
        all_coverage.append(coverage)

        if not args.quiet:
            ratio    = coverage["overall_ratio"]
            n_warn   = len(coverage["warnings"])
            warn_str = f"  ⚠️ {n_warn} low-coverage section(s)" if n_warn else ""
            flag     = "✅" if ratio >= COVERAGE_WARNING else "❌"
            print(
                f"  {flag}  {stem:<45}"
                f"  segs={len(segments):>4}"
                f"  cov={ratio:.1%}"
                + warn_str
            )

    # Write aggregate coverage warnings
    warnings_path = SEGMENTS_DIR / "_coverage_warnings.json"
    all_warnings = [
        {"stem": cov["stem"], **w}
        for cov in all_coverage
        for w in cov["warnings"]
    ]
    if all_warnings:
        SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
        warnings_path.write_text(
            json.dumps(all_warnings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # Summary
    total_segs  = sum(c["segment_count"] for c in all_coverage)
    ok_count    = sum(1 for c in all_coverage if c["overall_ratio"] >= COVERAGE_WARNING)
    warn_count  = sum(1 for c in all_coverage if c["overall_ratio"] < COVERAGE_WARNING)

    print(f"\n[📊]  Done: {done} processed | {skipped} skipped | {errors} errors")
    print(f"   Total segments : {total_segs}")
    print(f"   Coverage ≥ 85% : {ok_count}")
    if warn_count:
        print(f"   Coverage < 85% : {warn_count}  →  see {warnings_path}")
    if all_warnings and (args.stats or not args.quiet):
        print(f"\n   Low-coverage sections ({len(all_warnings)} total):")
        for w in all_warnings[:10]:
            print(f"   {w['stem']}#{w.get('segment_id','?')}  "
                  f"p.{w['page_from']}-{w['page_to']}  cov={w['ratio']:.1%}")
        if len(all_warnings) > 10:
            print(f"   ... and {len(all_warnings) - 10} more")
    print(f"\n   📁 Segments → corpus/segments/")
    if HAS_FITZ and not args.no_images:
        print(f"   🖼️  Images  → corpus/images/")


if __name__ == "__main__":
    main()
