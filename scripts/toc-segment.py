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

VERSION POLICY
  By default, only the LATEST version of each norm is processed.
  Older versions are skipped with a notice.
  Use --all-versions to process every available version.
  Use --list-versions to show what is available without processing.

  Version is parsed from the ETSI filename suffix:
    ts_119512v020201p  →  norm=ts_119512  version=(2,2,1)
  The highest tuple wins.

Segment JSON schema:
  {
    "id":          "ts_119512#7.3.1",
    "norm":        "ts_119512",
    "version":     "02.02.01",
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
    "tables":      [...],            // extracted table rows [{"header":[...], "rows":[[...]]}]
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
  python3 scripts/toc-segment.py                    # latest version per norm only (default)
  python3 scripts/toc-segment.py ts_119512          # latest version of ts_119512
  python3 scripts/toc-segment.py ts_119512v020201p  # exact version
  python3 scripts/toc-segment.py --all-versions     # all available versions
  python3 scripts/toc-segment.py --list-versions    # show available versions, no processing
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
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

CORPUS_ROOT    = Path("corpus")
TOC_DIR        = CORPUS_ROOT / "toc"
SEGMENTS_DIR   = CORPUS_ROOT / "segments"
IMAGES_DIR     = CORPUS_ROOT / "images"
DOWNLOAD_ROOTS = [
    Path("downloads/specs/TS"),
    Path("downloads/specs/EN"),
    Path("downloads/specs/TR"),
    Path("downloads/specs/SR"),
    Path("downloads/specs"),
]

COVERAGE_WARNING = 0.85   # warn if extracted chars < 85 % of PDF chars
IMAGE_SCALE      = 2.0    # render multiplier (≈ 144 DPI on A4)
HEADER_ZONE      = 0.10   # top 10 % of page
FOOTER_ZONE      = 0.09   # bottom 9 % of page

_HEADER_RE = re.compile(
    r"ETSI\s+(EN|TS|TR|GS|GR|EG)\s+\d|Draft\s+ETSI|^ETSI$",
    re.IGNORECASE | re.MULTILINE,
)
_FOOTER_RE = re.compile(
    r"^\d+$|ETSI\s*$|\d{4}-\d{2}$|\u00a9\s*ETSI|Publicly Available|Confidential",
    re.IGNORECASE | re.MULTILINE,
)
_RFC2119_RE = re.compile(
    r"\b(shall\s+not|shall|should\s+not|should|must\s+not|must"
    r"|required|recommended|may\s+not|may|optional)\b",
    re.IGNORECASE,
)
_XREF_RE = re.compile(
    r"\b(?:clause|section|annex)\s+([A-Z]?\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
_FIGURE_RE = re.compile(
    r"(?:Figure|Fig\.?)\s+(?P<num>[\d\-A-Z]+)[:\s]+(?P<caption>[^\n]{5,120})",
    re.IGNORECASE,
)
_ANNEX_RE  = re.compile(r"^Annex\s+[A-Z]", re.IGNORECASE)
_INFORM_RE = re.compile(r"\(informative\)|^NOTE\b|^EXAMPLE\b", re.IGNORECASE)
_BOILER_RE = re.compile(
    r"Intellectual Property|Essential patents|Important Notice"
    r"|Modal verbs terminology|Foreword|Introduction",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────────
# VERSION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def parse_version(stem: str) -> tuple[int, int, int]:
    """Extract (major, minor, patch) from ETSI stem.
    ts_119512v020201p → (2, 2, 1).  Returns (0,0,0) if unparseable.
    """
    m = re.search(r'v(\d{2})(\d{2})(\d{2})p', stem)
    return (int(m[1]), int(m[2]), int(m[3])) if m else (0, 0, 0)


def version_str(stem: str) -> str:
    """ts_119512v020201p → '02.02.01'."""
    v = parse_version(stem)
    return ".".join(f"{x:02d}" for x in v)


def norm_id_from_stem(stem: str) -> str:
    """ts_119512v020201p → ts_119512."""
    return re.sub(r'v\d{6}p$', '', stem)


def resolve_latest_toc_files(toc_files: list[Path]) -> list[Path]:
    """
    Given a list of .toc.json paths, return only the latest version for
    each norm (highest version tuple wins).

    Example:
      ts_119512v010201p.toc.json  → skipped
      ts_119512v020201p.toc.json  → kept   ← latest
    """
    # Group by norm_id
    best: dict[str, tuple[tuple[int, int, int], Path]] = {}
    for p in toc_files:
        stem    = p.name.replace(".toc.json", "")
        norm_id = norm_id_from_stem(stem)
        ver     = parse_version(stem)
        if norm_id not in best or ver > best[norm_id][0]:
            best[norm_id] = (ver, p)
    return sorted(p for _, p in best.values())


def list_versions(toc_files: list[Path]) -> None:
    """Print a table of all available versions grouped by norm."""
    groups: dict[str, list[tuple[tuple[int, int, int], str]]] = {}
    for p in toc_files:
        stem    = p.name.replace(".toc.json", "")
        norm_id = norm_id_from_stem(stem)
        ver     = parse_version(stem)
        groups.setdefault(norm_id, []).append((ver, stem))

    print(f"\n{'Norm':<30}  {'Available versions'}")
    print("-" * 70)
    for norm_id in sorted(groups):
        entries = sorted(groups[norm_id], reverse=True)
        latest  = entries[0][1]
        for i, (ver, stem) in enumerate(entries):
            marker = " ← latest" if i == 0 else ""
            label  = norm_id if i == 0 else ""
            print(f"  {label:<28}  {version_str(stem)}  ({stem}){marker}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# PDF PATH RESOLUTION
# ──────────────────────────────────────────────────────────────────────────────

def find_pdf(stem: str) -> Path | None:
    """Search known download directories for <stem>.pdf."""
    for root in DOWNLOAD_ROOTS:
        candidate = root / (stem + ".pdf")
        if candidate.is_file():
            return candidate
    return None


# ──────────────────────────────────────────────────────────────────────────────
# PAGE TEXT EXTRACTION (zero-loss)
# ──────────────────────────────────────────────────────────────────────────────

def extract_page_text(page) -> dict:
    """
    Extract ALL text from a page:
    - body zone (header/footer geometry stripped)
    - hf zone   (header/footer text retained, never discarded)
    - tables    (extracted separately so no cell is lost)
    Returns {"body", "hf", "tables", "chars_total", "chars_body"}.
    """
    h            = float(page.height)
    header_y_max = h * HEADER_ZONE
    footer_y_min = h * (1 - FOOTER_ZONE)

    body_chars, hf_chars = [], []
    for ch in page.chars:
        if not ch.get("text", "").strip():
            continue
        y_mid = (ch["top"] + ch["bottom"]) / 2
        (hf_chars if (y_mid < header_y_max or y_mid > footer_y_min) else body_chars).append(ch)

    body_crop = page.within_bbox((0, header_y_max, page.width, footer_y_min), relative=False)
    body_text = body_crop.extract_text(layout=True) or ""

    hf_crop = page.filter(lambda obj: obj.get("object_type") == "char" and (
        (obj.get("top", 0) + obj.get("bottom", 0)) / 2 < header_y_max
        or (obj.get("top", 0) + obj.get("bottom", 0)) / 2 > footer_y_min
    ))
    hf_text = (hf_crop.extract_text() or "") if hf_chars else ""

    tables = []
    for tbl in body_crop.extract_tables():
        if not tbl:
            continue
        tables.append({
            "header": [cell or "" for cell in tbl[0]],
            "rows":   [[cell or "" for cell in row] for row in tbl[1:]],
        })
        for row in tbl:
            body_text += "\n" + " | ".join(cell or "" for cell in row)

    return {
        "body":        body_text.strip(),
        "hf":          hf_text.strip(),
        "tables":      tables,
        "chars_total": len(page.chars),
        "chars_body":  len(body_chars),
    }


# ──────────────────────────────────────────────────────────────────────────────
# IMAGE EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def extract_images_for_pdf(pdf_path: Path, stem: str) -> list[dict]:
    """Render pages as PNG and extract embedded rasters. Requires pymupdf."""
    if not HAS_FITZ:
        return []
    out_dir = IMAGES_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        print(f"  [images] Cannot open {pdf_path.name}: {exc}", file=sys.stderr)
        return []

    for idx in range(len(doc)):
        pg_nr = idx + 1
        page  = doc[idx]

        # Full-page render
        pix  = page.get_pixmap(matrix=fitz.Matrix(IMAGE_SCALE, IMAGE_SCALE), alpha=False)
        path = out_dir / f"page_{pg_nr:03d}.png"
        pix.save(str(path))
        meta.append({"type": "page_render", "page": pg_nr, "path": str(path), "caption": None})

        # Embedded rasters (deduplicated by SHA-1)
        for img_info in page.get_images(full=True):
            try:
                b   = doc.extract_image(img_info[0])
                ext = b.get("ext", "png")
                h   = hashlib.sha1(b["image"]).hexdigest()[:12]
                p   = out_dir / f"{h}.{ext}"
                if not p.exists():
                    p.write_bytes(b["image"])
                meta.append({"type": "embedded", "page": pg_nr, "path": str(p), "caption": None})
            except Exception:
                pass
    doc.close()
    return meta


def _attach_figure_captions(images: list[dict], page_texts: dict[int, str]) -> None:
    for img in images:
        if img["caption"] is not None:
            continue
        m = _FIGURE_RE.search(page_texts.get(img["page"], ""))
        if m:
            img["caption"] = f"Figure {m.group('num')}: {m.group('caption').strip()}"


# ──────────────────────────────────────────────────────────────────────────────
# SECTION TYPE CLASSIFIER
# ──────────────────────────────────────────────────────────────────────────────

def classify_section(entry: dict) -> str:
    num, title = (entry.get("num") or "").strip(), (entry.get("title") or "").strip()
    combined   = f"{num} {title}"
    if _BOILER_RE.search(combined):  return "boilerplate"
    if _ANNEX_RE.search(combined):   return "inform" if _INFORM_RE.search(title) else "annex"
    if _INFORM_RE.search(title):     return "inform"
    return "norm"


# ──────────────────────────────────────────────────────────────────────────────
# CORE SEGMENTATION
# ──────────────────────────────────────────────────────────────────────────────

def segment_pdf_with_toc(
    pdf_path: Path,
    toc_data: dict,
    stem: str,
    extract_images: bool = True,
) -> tuple[list[dict], dict]:
    """
    Segment a PDF using TOC page-ranges as the structural authority.
    Returns (segments, coverage_report).
    """
    norm_id = norm_id_from_stem(stem)
    version = version_str(stem)
    toc     = toc_data.get("toc", [])
    if not toc:
        return [], {"error": "empty TOC"}

    # 1. Extract all pages up front
    page_extracts: dict[int, dict] = {}
    total_chars_pdf = total_chars_body = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        for page in pdf.pages:
            ex = extract_page_text(page)
            page_extracts[page.page_number] = ex
            total_chars_pdf  += ex["chars_total"]
            total_chars_body += ex["chars_body"]

    # 2. Images (optional)
    all_images: list[dict] = []
    if extract_images:
        all_images = extract_images_for_pdf(pdf_path, stem)
        _attach_figure_captions(all_images, {pg: ex["body"] for pg, ex in page_extracts.items()})

    # 3. Page-range map from TOC (sorted by page then section number)
    sorted_toc = sorted(toc, key=lambda e: (e.get("page", 0), e.get("num", "")))
    page_ranges: list[tuple[dict, int, int]] = []
    for i, entry in enumerate(sorted_toc):
        p_from = entry.get("page", 1)
        p_to   = (sorted_toc[i + 1]["page"] - 1
                  if i + 1 < len(sorted_toc) else total_pages)
        page_ranges.append((entry, p_from, max(p_from, p_to)))

    # 4. Prev / next / parent maps
    nums     = [e.get("num", "") for e in sorted_toc]
    prev_map = {nums[i]: (nums[i - 1] if i > 0 else None)     for i in range(len(nums))}
    next_map = {nums[i]: (nums[i + 1] if i + 1 < len(nums) else None) for i in range(len(nums))}

    def parent_num(num: str) -> str | None:
        parts = num.rsplit(".", 1)
        return parts[0] if len(parts) > 1 and parts[0] else None

    # 5. Build segments
    segments: list[dict] = []
    coverage_warnings: list[dict] = []

    for entry, p_from, p_to in page_ranges:
        num      = entry.get("num", "")
        title    = entry.get("title", "")
        depth    = entry.get("depth", len(num.split(".")))
        sec_type = classify_section(entry)
        parent   = parent_num(num)
        seg_id   = f"{norm_id}#{num}" if num else f"{norm_id}#p{p_from}"

        page_texts : list[str]  = []
        page_tables: list[dict] = []
        seg_chars_pdf = seg_chars_body = 0

        for pg in range(p_from, p_to + 1):
            ex = page_extracts.get(pg)
            if ex is None:
                continue
            page_texts.append(ex["body"])
            page_tables.extend(ex["tables"])
            seg_chars_pdf  += ex["chars_total"]
            seg_chars_body += ex["chars_body"]

        body_text = "\f".join(t for t in page_texts if t)
        # Stitch mid-sentence page breaks
        body_text = re.sub(r'([^.!?:\n])\f([a-z])', r'\1 \2', body_text)

        cov_ratio = (seg_chars_body / seg_chars_pdf) if seg_chars_pdf else 1.0
        if seg_chars_pdf > 0 and cov_ratio < COVERAGE_WARNING:
            coverage_warnings.append({
                "segment_id": seg_id, "page_from": p_from, "page_to": p_to,
                "chars_pdf": seg_chars_pdf, "chars_body": seg_chars_body,
                "ratio": round(cov_ratio, 3),
            })

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
            "images":            [img for img in all_images if p_from <= img["page"] <= p_to],
            "normative_keywords": list({m.group(1).lower() for m in _RFC2119_RE.finditer(body_text)}),
            "cross_refs":        list({m.group(1) for m in _XREF_RE.finditer(body_text)}),
            "coverage": {
                "chars_in_pdf":    seg_chars_pdf,
                "chars_extracted": seg_chars_body,
                "ratio":           round(cov_ratio, 3),
            },
        })

    coverage_report = {
        "stem":             stem,
        "norm_id":          norm_id,
        "version":          version,
        "total_pages":      total_pages,
        "total_chars_pdf":  total_chars_pdf,
        "total_chars_body": total_chars_body,
        "overall_ratio":    round(total_chars_body / total_chars_pdf, 3) if total_chars_pdf else 0.0,
        "segment_count":    len(segments),
        "warnings":         coverage_warnings,
    }
    return segments, coverage_report


# ──────────────────────────────────────────────────────────────────────────────
# FILE I/O
# ──────────────────────────────────────────────────────────────────────────────

def write_segments(stem: str, segments: list[dict], coverage: dict) -> Path:
    """Write corpus/segments/<stem>/meta.json + sec_<num>.json files."""
    out_dir = SEGMENTS_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "meta.json").write_text(json.dumps({
        "stem":          stem,
        "norm_id":       coverage["norm_id"],
        "version":       coverage["version"],
        "segment_count": len(segments),
        "segmented_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage":      coverage,
        "segment_ids":   [s["id"] for s in segments],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    for seg in segments:
        safe = re.sub(r"[^a-z0-9]+", "_", seg["num"].lower()).strip("_")
        name = f"sec_{safe}.json" if safe else f"sec_p{seg['page_from']}.json"
        (out_dir / name).write_text(
            json.dumps(seg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return out_dir


def iter_all_toc_jsons() -> list[Path]:
    """Return all *.toc.json files in corpus/toc/ sorted alphabetically."""
    if not TOC_DIR.is_dir():
        return []
    return sorted(TOC_DIR.glob("*.toc.json"))


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="TOC-guided zero-loss ETSI PDF segmentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/toc-segment.py                    # latest version per norm (default)
  python3 scripts/toc-segment.py ts_119512          # latest version of ts_119512
  python3 scripts/toc-segment.py ts_119512v020201p  # exact version
  python3 scripts/toc-segment.py --all-versions     # every version in corpus/toc/
  python3 scripts/toc-segment.py --list-versions    # show versions, no processing
  python3 scripts/toc-segment.py --force            # overwrite existing
  python3 scripts/toc-segment.py --no-images        # skip image extraction
""",
    )
    parser.add_argument(
        "stems", nargs="*",
        help="Norm stems to process (e.g. ts_119512 or ts_119512v020201p). "
             "Default: latest version of every norm in corpus/toc/.",
    )
    parser.add_argument("--all-versions", action="store_true",
                        help="Process ALL versions, not just the latest per norm")
    parser.add_argument("--list-versions", action="store_true",
                        help="List available versions and exit (no processing)")
    parser.add_argument("--force",      "-f", action="store_true",
                        help="Overwrite existing segment directories")
    parser.add_argument("--no-images",  action="store_true",
                        help="Skip image extraction (faster)")
    parser.add_argument("--stats",      action="store_true",
                        help="Print coverage report after processing")
    parser.add_argument("--quiet",      "-q", action="store_true",
                        help="Suppress per-file output")
    args = parser.parse_args()

    if not HAS_FITZ and not args.no_images:
        print("[WARN] pymupdf not installed — image extraction disabled. "
              "Install with: pip install pymupdf", file=sys.stderr)

    all_toc = iter_all_toc_jsons()
    if not all_toc:
        sys.exit(f"[ERROR] No TOC JSONs found in {TOC_DIR}. Run: npm run toc")

    # --list-versions: just print and exit
    if args.list_versions:
        list_versions(all_toc)
        return

    # Resolve which TOC files to process
    if args.stems:
        toc_files: list[Path] = []
        for s in args.stems:
            matches = list(TOC_DIR.glob(f"{s}*.toc.json"))
            if not matches:
                print(f"[WARN] No TOC JSON found for: {s}", file=sys.stderr)
                continue
            if args.all_versions:
                toc_files.extend(matches)
            else:
                # Pick only the latest match for this stem
                toc_files.extend(resolve_latest_toc_files(matches))
    else:
        toc_files = (
            all_toc if args.all_versions
            else resolve_latest_toc_files(all_toc)
        )

    if not toc_files:
        sys.exit("[ERROR] No matching TOC files to process.")

    # Show what was filtered out
    if not args.all_versions and not args.stems:
        all_stems   = {p.name.replace(".toc.json", "") for p in all_toc}
        keep_stems  = {p.name.replace(".toc.json", "") for p in toc_files}
        skipped_old = sorted(all_stems - keep_stems)
        if skipped_old and not args.quiet:
            print(f"[INFO] Skipping {len(skipped_old)} older version(s) "
                  f"(use --all-versions to include):")
            for s in skipped_old:
                print(f"       ↳ {s}")
            print()

    print(f"[INFO] Processing {len(toc_files)} PDF(s)  →  corpus/segments/")
    print(f"       Versions   : {'all' if args.all_versions else 'latest only'}")
    print(f"       Images     : {'disabled' if (not HAS_FITZ or args.no_images) else 'enabled'}")
    print()

    all_coverage: list[dict] = []
    done = skipped = errors = 0

    for toc_path in toc_files:
        stem    = toc_path.name.replace(".toc.json", "")
        seg_dir = SEGMENTS_DIR / stem

        if not args.force and (seg_dir / "meta.json").exists():
            if not args.quiet:
                print(f"  ⏭️  {stem}  (already segmented, use --force to redo)")
            skipped += 1
            continue

        pdf_path = find_pdf(stem)
        if pdf_path is None:
            print(f"  ⚠️  {stem}  — PDF not found", file=sys.stderr)
            errors += 1
            continue

        try:
            toc_data = json.loads(toc_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ❌  {stem}: cannot read TOC JSON: {exc}", file=sys.stderr)
            errors += 1
            continue

        try:
            segments, coverage = segment_pdf_with_toc(
                pdf_path, toc_data, stem,
                extract_images=(HAS_FITZ and not args.no_images),
            )
        except Exception as exc:
            print(f"  ❌  {stem}: segmentation error: {exc}", file=sys.stderr)
            errors += 1
            continue

        try:
            write_segments(stem, segments, coverage)
        except Exception as exc:
            print(f"  ❌  {stem}: write error: {exc}", file=sys.stderr)
            errors += 1
            continue

        done += 1
        all_coverage.append(coverage)

        if not args.quiet:
            ratio    = coverage["overall_ratio"]
            n_warn   = len(coverage["warnings"])
            warn_str = f"  ⚠️  {n_warn} low-coverage section(s)" if n_warn else ""
            flag     = "✅" if ratio >= COVERAGE_WARNING else "❌"
            print(
                f"  {flag}  {stem:<48}  v{coverage['version']}"
                f"  segs={len(segments):>4}  cov={ratio:.1%}"
                + warn_str
            )

    # Coverage warnings file
    all_warnings = [
        {"stem": c["stem"], **w}
        for c in all_coverage for w in c["warnings"]
    ]
    if all_warnings:
        SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
        (SEGMENTS_DIR / "_coverage_warnings.json").write_text(
            json.dumps(all_warnings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Summary
    total_segs = sum(c["segment_count"] for c in all_coverage)
    ok_count   = sum(1 for c in all_coverage if c["overall_ratio"] >= COVERAGE_WARNING)
    bad_count  = sum(1 for c in all_coverage if c["overall_ratio"] < COVERAGE_WARNING)

    print(f"\n[📊]  Done: {done} processed | {skipped} skipped | {errors} errors")
    print(f"      Total segments  : {total_segs}")
    print(f"      Coverage ≥ 85%  : {ok_count}")
    if bad_count:
        print(f"      Coverage < 85%  : {bad_count}  →  see corpus/segments/_coverage_warnings.json")
    if all_warnings and (args.stats or not args.quiet):
        print(f"\n      Low-coverage sections ({len(all_warnings)} total):")
        for w in all_warnings[:10]:
            print(f"      {w['stem']}#{w.get('segment_id','?')}  "
                  f"p.{w['page_from']}-{w['page_to']}  cov={w['ratio']:.1%}")
        if len(all_warnings) > 10:
            print(f"      … and {len(all_warnings) - 10} more")
    print(f"\n      📁 Segments  →  corpus/segments/")
    if HAS_FITZ and not args.no_images:
        print(f"      🖼️  Images    →  corpus/images/")


if __name__ == "__main__":
    main()
