#!/usr/bin/env python3
"""
para-tune.py  —  Adaptive paragraph-boundary calibrator for ETSI PDFs

Problem
-------
ETSI PDFs come from different Word-to-PDF renderers and font setups, so the
vertical gap between wrapped lines of the *same* paragraph varies across docs.
A single global _GAP_THRESHOLD in pdf-segment.py cannot be optimal for all
files.  This script:

  1. Measures per-document segment quality (median length, short-seg ratio).
  2. Grid-searches the best GAP_THRESHOLD for each PDF that needs improvement.
  3. When the heuristic alone cannot reach the quality target it calls a local
     Ollama model to classify a sample of boundary candidates and derives a
     refined merge rule from the responses.
  4. Writes learned per-stem overrides to corpus/para-tune/learned-rules.json
     so that pdf-segment.py can read them on startup.

Quality target
--------------
  median segment length  >= MIN_MEDIAN_CHARS  (default 150)
  fraction of segs < 100 chars  <= MAX_SHORT_FRAC  (default 0.25)
  absolute segment count >= MIN_SEGS_ABSOLUTE (default 10)

Collapse guard
--------------
  A result is considered collapsed if ANY of the following:
    - n_segs < MIN_SEGS_ABSOLUTE (absolute floor, catches n=5 cases)
    - n_segs < MIN_SEGS_RATIO * baseline_n (relative floor vs gap=20 baseline)
    - median > MAX_SEG_MEDIAN (giant blocks = table rows merged)
  The collapse check is applied both during grid search AND when evaluating
  the current rule (so a previously-written collapsed rule is re-tuned).

Table masking
-------------
  Before gap-based line grouping, chars whose midpoint falls inside a
  detected table bounding-box are excluded.  This prevents table-cell gaps
  (typically 16-22pt) from biasing the gap calibration.

  Only the "lines" strategy is used (bordered tables, ruled grids).  The
  "text" strategy is intentionally omitted: it treats column-aligned body
  text as a table, masking the entire page and producing n=1 for all gaps.

  edge_min_length=20pt filters decorative rules, thin underlines under
  section headings, and other short vector segments that are not table edges.

  No area guard is applied: full-page ETSI tables (Capability-matrices,
  Requirement tables) are legitimate and must not be excluded.

Ollama fallback
---------------
Requires `ollama` running locally (http://localhost:11434).
Default model: llama3.  Override with --model.

Learned rules format  (corpus/para-tune/learned-rules.json)
-----------------------------------------------------------
{
  "ts_11914402v020101p": {
    "gap_threshold": 18,
    "merge_endings": [";", ","],
    "no_merge_starts": ["NOTE"],
    "tuned_at": "2026-06-13T21:00:00Z",
    "method": "grid"
  },
  ...
}

Usage
-----
  python3 scripts/para-tune.py                    # all PDFs in corpus
  python3 scripts/para-tune.py --pdf downloads/specs/TS/ts_11914402v020101p.pdf
  python3 scripts/para-tune.py --no-ai            # heuristic / grid only
  python3 scripts/para-tune.py --model mistral    # different Ollama model
  python3 scripts/para-tune.py --stats            # metrics only, no tuning
  python3 scripts/para-tune.py --apply            # re-segment with learned rules

Dependencies
  pip install pdfplumber requests
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

RULES_PATH      = Path("corpus/para-tune/learned-rules.json")
CORPUS_SEG_DIR  = Path("corpus/specs/_segments")
DOWNLOAD_ROOTS = [
    Path("downloads/specs/TS"),
    Path("downloads/specs/EN"),
    Path("downloads/specs/TR"),
    Path("downloads/specs/SR"),
    Path("downloads/specs"),
]

MIN_MEDIAN_CHARS  = 150   # quality target: median segment ≥ this
MAX_SHORT_FRAC    = 0.25  # quality target: fraction <100 chars ≤ this
MIN_SEGS_ABSOLUTE = 10    # collapse guard: fewer than this = definitely collapsed

# Relative collapse guard (vs gap=20 baseline)
MIN_SEGS_RATIO  = 0.25   # candidate must keep ≥ 25 % of baseline segment count
MAX_SEG_MEDIAN  = 1200   # median > 1200 chars almost certainly means table collapse

GAP_GRID = [8, 12, 16, 20, 24, 28, 32, 40]  # candidate thresholds to try

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3"

# Table-detection: "lines" strategy only.
# edge_min_length=20pt: filters decorative rules / underlines (< ~1.5 cm).
# No area guard: full-page ETSI tables are legitimate.
_TABLE_SETTINGS_LINES = {
    "vertical_strategy":   "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance":  3,
    "join_tolerance":  3,
    "edge_min_length": 20,
}

_RFC2119_RE = re.compile(
    r"\b(shall\s+not|shall|should\s+not|should|must\s+not|must"
    r"|required|recommended|may\s+not|may|optional)\b",
    re.IGNORECASE,
)
_SENTENCE_END_RE  = re.compile(r"[.!?:]\s*$")
_NEW_ITEM_RE = re.compile(
    r"^(\d+[\.]\s|\([a-z]\)\s|[-–•]\s|NOTE\b|EXAMPLE\b)",
    re.IGNORECASE,
)
_TOC_LINE_RE = re.compile(r"^.{2,60}[\.\ ]{4,}\d{1,4}\s*$", re.MULTILINE)
_SECTION_RE  = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$"
)


# ─────────────────────────────────────────────────────────────────────────────
# Learned-rules I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_rules() -> dict:
    if RULES_PATH.exists():
        try:
            return json.loads(RULES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_rules(rules: dict) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Table-region detection + char masking
# ─────────────────────────────────────────────────────────────────────────────

def _get_table_bboxes(page) -> list[tuple[float, float, float, float]]:
    try:
        tables = page.find_tables(table_settings=_TABLE_SETTINGS_LINES)
        if not tables:
            return []
        return [t.bbox for t in tables]
    except Exception:
        return []


def _char_in_table(
    ch: dict,
    bboxes: list[tuple[float, float, float, float]],
) -> bool:
    if not bboxes:
        return False
    mid_x = (ch["x0"] + ch["x1"]) / 2
    mid_y = (ch["top"] + ch["bottom"]) / 2
    return any(
        x0 <= mid_x < x1 and top <= mid_y < bottom
        for (x0, top, x1, bottom) in bboxes
    )


# ─────────────────────────────────────────────────────────────────────────────
# Minimal PDF block extractor (mirrors pdf-segment.py logic, gap-parameterised)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_raw_lines(
    page,
    table_bboxes: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[int, list[dict]]]:
    lines: dict[int, list[dict]] = {}
    for ch in (page.chars or []):
        if table_bboxes and _char_in_table(ch, table_bboxes):
            continue
        y_mid = int((ch["top"] + ch["bottom"]) / 2)
        lines.setdefault(y_mid, []).append(ch)
    return sorted(lines.items())


def _lines_to_blocks(lines: list[tuple[int, list[dict]]], gap: int, page_height: float
                    ) -> list[dict]:
    blocks: list[dict] = []
    cur_chars: list[dict] = []
    cur_ys:    list[int]  = []

    def _flush():
        if not cur_chars:
            return
        text = "".join(
            c["text"] for c in sorted(cur_chars, key=lambda c: (c["top"], c["x0"]))
        )
        text = re.sub(r" {2,}", " ", text).strip()
        if not text:
            return
        y_top = float(min(c["top"]    for c in cur_chars))
        y_bot = float(max(c["bottom"] for c in cur_chars))
        sizes = [c.get("size", 0) for c in cur_chars if c.get("size")]
        avg_size = sum(sizes) / len(sizes) if sizes else 0.0
        blocks.append({"text": text, "y_top": y_top, "y_bot": y_bot,
                       "avg_size": avg_size, "page_height": page_height})

    for y, chs in lines:
        if cur_ys and (y - cur_ys[-1]) > gap:
            _flush()
            cur_chars, cur_ys = [], []
        cur_chars.extend(chs)
        cur_ys.append(y)
    _flush()
    return sorted(blocks, key=lambda b: b["y_top"])


def _quick_classify(block: dict, page_nr: int, page_height: float) -> str:
    text = block["text"]
    y_frac_top = 1.0 - (block["y_bot"] / page_height)
    y_frac_bot = 1.0 - (block["y_top"] / page_height)
    if y_frac_top < 0.10 and page_nr > 1:
        return "HEADER"
    if y_frac_bot > 0.91 and page_nr > 1:
        return "FOOTER"
    if len(_TOC_LINE_RE.findall(text)) >= 3:
        return "TOC"
    if _SECTION_RE.match(text.strip()):
        return "SECTION"
    if re.match(r"^(Figure|Table|NOTE|EXAMPLE)\s+\d", text, re.IGNORECASE):
        return "TABLE"
    if _RFC2119_RE.search(text):
        return "NORM"
    if re.search(r"\bNOTE\b|\bEXAMPLE\b|informative", text, re.IGNORECASE):
        return "INFORM"
    return "OTHER"


def _merge_body_blocks(blocks: list[dict], types: list[str],
                       extra_endings: list[str] | None = None,
                       extra_no_starts: list[str] | None = None
                       ) -> tuple[list[dict], list[str]]:
    MERGEABLE = {"NORM", "INFORM"}
    no_start_pat = _NEW_ITEM_RE
    if extra_no_starts:
        combined = _NEW_ITEM_RE.pattern + "|".join(
            re.escape(s) for s in extra_no_starts
        )
        no_start_pat = re.compile(combined, re.IGNORECASE)

    def _ends_sentence(text: str) -> bool:
        if _SENTENCE_END_RE.search(text):
            return True
        if extra_endings:
            for e in extra_endings:
                if text.rstrip().endswith(e):
                    return False
        return False

    out_b: list[dict] = []
    out_t: list[str]  = []
    for blk, stype in zip(blocks, types):
        if (
            out_b
            and stype in MERGEABLE
            and out_t[-1] == stype
            and not _ends_sentence(out_b[-1]["text"])
            and not no_start_pat.match(blk["text"])
        ):
            prev = out_b[-1]
            out_b[-1] = {**prev,
                         "text":  prev["text"].rstrip() + " " + blk["text"].lstrip(),
                         "y_bot": blk["y_bot"]}
        else:
            out_b.append(blk)
            out_t.append(stype)
    return out_b, out_t


# ─────────────────────────────────────────────────────────────────────────────
# Quality metrics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SegQuality:
    stem:        str
    n_segs:      int
    median_len:  float
    short_frac:  float
    tiny_frac:   float
    gap_used:    int
    baseline_n:  int = 0

    @property
    def ok(self) -> bool:
        """Meets quality target AND is not collapsed."""
        if self.collapsed:
            return False
        return self.median_len >= MIN_MEDIAN_CHARS and self.short_frac <= MAX_SHORT_FRAC

    @property
    def collapsed(self) -> bool:
        """True if the result is clearly over-merged / collapsed."""
        # Absolute floor: fewer than MIN_SEGS_ABSOLUTE segments is always collapsed
        if self.n_segs < MIN_SEGS_ABSOLUTE:
            return True
        # Relative floor: lost too many segments vs gap=20 baseline
        if self.baseline_n > 0 and self.n_segs < self.baseline_n * MIN_SEGS_RATIO:
            return True
        # Giant blocks: median > 1200 chars means table rows have been merged
        if self.median_len > MAX_SEG_MEDIAN:
            return True
        return False

    def __str__(self) -> str:
        flag = "✓" if self.ok else "✗"
        collapse_tag = "  [⚠️ collapsed]" if self.collapsed else ""
        return (
            f"{flag} {self.stem[:45]:45}  "
            f"n={self.n_segs:4}  med={self.median_len:>6.0f}  "
            f"<100={self.short_frac*100:4.1f}%  gap={self.gap_used}"
            f"{collapse_tag}"
        )


def _measure_pdf(
    pdf_path: Path,
    gap: int,
    extra_endings: list[str] | None = None,
    extra_no_starts: list[str] | None = None,
    mask_tables: bool = True,
) -> SegQuality:
    all_texts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            table_bboxes = _get_table_bboxes(page) if mask_tables else []
            raw_lines = _extract_raw_lines(page, table_bboxes)
            blocks    = _lines_to_blocks(raw_lines, gap, float(page.height))
            types     = [_quick_classify(b, page.page_number, float(page.height))
                         for b in blocks]
            blocks, types = _merge_body_blocks(blocks, types, extra_endings, extra_no_starts)
            for blk, stype in zip(blocks, types):
                if stype in ("NORM", "INFORM"):
                    all_texts.append(blk["text"])

    if not all_texts:
        return SegQuality(stem=pdf_path.stem, n_segs=0, median_len=0,
                          short_frac=1.0, tiny_frac=1.0, gap_used=gap)
    lens = [len(t) for t in all_texts]
    return SegQuality(
        stem=pdf_path.stem,
        n_segs=len(lens),
        median_len=statistics.median(lens),
        short_frac=sum(1 for l in lens if l < 100) / len(lens),
        tiny_frac=sum(1 for l in lens if l < 50)  / len(lens),
        gap_used=gap,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Grid search
# ─────────────────────────────────────────────────────────────────────────────

def grid_search(pdf_path: Path, verbose: bool = True) -> tuple[int, SegQuality]:
    """
    Try each GAP in GAP_GRID.  Return (best_gap, best_quality).

    Selection logic (in priority order):
      1. Exclude collapsed candidates.
      2. Among passing non-collapsed candidates, prefer the SMALLEST gap.
      3. If none pass, pick non-collapsed with highest median.
      4. If all collapsed, fall back to gap=20.
    """
    baseline_gap = 20
    baseline_q   = _measure_pdf(pdf_path, baseline_gap)
    baseline_n   = baseline_q.n_segs or 1

    results: list[tuple[int, SegQuality]] = []
    for gap in GAP_GRID:
        q = _measure_pdf(pdf_path, gap)
        q.baseline_n = baseline_n
        results.append((gap, q))
        if verbose:
            collapse_marker = "  ⚠️ collapsed" if q.collapsed else ""
            print(f"     gap={gap:2d}  {q}{collapse_marker}")

    sane = [(g, q) for g, q in results if not q.collapsed]

    if not sane:
        if verbose:
            print("   ⚠️  All grid candidates collapsed — keeping gap=20 (best-effort)")
        baseline_q.baseline_n = baseline_n
        return baseline_gap, baseline_q

    passing = [(g, q) for g, q in sane if q.ok]
    if passing:
        best_gap, best_q = min(passing, key=lambda x: x[0])
    else:
        best_gap, best_q = max(sane, key=lambda x: x[1].median_len)

    return best_gap, best_q


# ─────────────────────────────────────────────────────────────────────────────
# Ollama fallback
# ─────────────────────────────────────────────────────────────────────────────

def _ollama_available(model: str) -> bool:
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            names = [m.get("name", "") for m in data.get("models", [])]
            return any(model in n for n in names)
    except Exception:
        return False


def _ollama_classify_boundary(
    text_before: str,
    text_after:  str,
    model: str = OLLAMA_MODEL,
) -> str:
    prompt = (
        "You are analysing extracted text from a PDF standards document (ETSI).\n"
        "Given two adjacent text fragments, decide whether there is a paragraph "
        "BREAK between them or whether the second fragment is a LINE-WRAP "
        "continuation of the first.\n\n"
        f"Fragment A: {text_before[-200:]!r}\n"
        f"Fragment B: {text_after[:200]!r}\n\n"
        "Answer with exactly one word: BREAK or WRAP."
    )
    payload = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 4},
    }).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            answer = result.get("response", "").strip().upper()
            return "BREAK" if "BREAK" in answer else "WRAP"
    except Exception:
        return "BREAK"


def ollama_tune(
    pdf_path:   Path,
    best_gap:   int,
    model:      str = OLLAMA_MODEL,
    n_samples:  int = 40,
    verbose:    bool = True,
) -> dict:
    if verbose:
        print(f"   🤖  Ollama fallback ({model}) — sampling {n_samples} boundaries...")

    candidates: list[tuple[str, str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            table_bboxes = _get_table_bboxes(page)
            raw_lines = _extract_raw_lines(page, table_bboxes)
            blocks    = _lines_to_blocks(raw_lines, best_gap, float(page.height))
            types     = [_quick_classify(b, page.page_number, float(page.height))
                         for b in blocks]
            for i in range(len(blocks) - 1):
                if (types[i] in ("NORM", "INFORM")
                        and types[i + 1] in ("NORM", "INFORM")
                        and not _SENTENCE_END_RE.search(blocks[i]["text"])
                        and len(blocks[i]["text"]) < 120):
                    candidates.append((blocks[i]["text"], blocks[i + 1]["text"]))

    if not candidates:
        return {"gap_threshold": best_gap, "method": "grid"}

    step = max(1, len(candidates) // n_samples)
    sample = candidates[::step][:n_samples]

    wraps  = 0
    breaks = 0
    wrap_endings:  list[str] = []
    break_endings: list[str] = []

    for before, after in sample:
        verdict = _ollama_classify_boundary(before, after, model)
        last_char = before.rstrip()[-1] if before.rstrip() else ""
        if verdict == "WRAP":
            wraps += 1
            if last_char and last_char not in ".!?:" and last_char not in wrap_endings:
                wrap_endings.append(last_char)
        else:
            breaks += 1
            if last_char and last_char not in break_endings:
                break_endings.append(last_char)

    if verbose:
        print(f"   🤖  Results: WRAP={wraps}  BREAK={breaks}")
        print(f"        wrap-endings:  {wrap_endings[:10]}")
        print(f"        break-endings: {break_endings[:10]}")

    learned_endings = [
        e for e in wrap_endings
        if e not in break_endings and e not in ".!?:"
    ]

    return {
        "gap_threshold":    best_gap,
        "merge_endings":    learned_endings,
        "no_merge_starts":  [],
        "method":           "ollama",
        "ollama_model":     model,
        "sample_size":      len(sample),
        "wrap_count":       wraps,
        "break_count":      breaks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-PDF tuning entry point
# ─────────────────────────────────────────────────────────────────────────────

def tune_pdf(
    pdf_path:  Path,
    rules:     dict,
    use_ai:    bool = True,
    model:     str  = OLLAMA_MODEL,
    verbose:   bool = True,
) -> dict | None:
    stem = pdf_path.stem
    if verbose:
        print(f"\n{'─'*60}")
        print(f"  {stem}")

    existing   = rules.get(stem, {})
    cur_gap    = existing.get("gap_threshold", 20)
    cur_extra  = existing.get("merge_endings", [])
    cur_nostrt = existing.get("no_merge_starts", [])

    q_current = _measure_pdf(pdf_path, cur_gap, cur_extra, cur_nostrt)
    if verbose:
        print(f"  Current : {q_current}")

    # Skip only if genuinely OK (not collapsed AND meets targets)
    if q_current.ok:
        if verbose:
            print("  → already meets quality target, skipping")
        return None

    # Collapsed current rule gets a fresh grid search
    if q_current.collapsed and verbose:
        print("  ⚠️  Current rule is collapsed — re-tuning")

    if verbose:
        print("  Grid search:")
    best_gap, best_q = grid_search(pdf_path, verbose=verbose)
    if verbose:
        print(f"  Best gap: {best_gap}  →  {best_q}")

    if best_q.ok:
        rule = {
            "gap_threshold":   best_gap,
            "merge_endings":   [],
            "no_merge_starts": [],
            "method":          "grid",
            "tuned_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics": {
                "median_len":  best_q.median_len,
                "short_frac":  best_q.short_frac,
                "n_segs":      best_q.n_segs,
            },
        }
        rules[stem] = rule
        return rule

    if use_ai and _ollama_available(model):
        rule = ollama_tune(pdf_path, best_gap, model=model, verbose=verbose)
        q_ai = _measure_pdf(
            pdf_path, rule["gap_threshold"],
            rule.get("merge_endings"),
            rule.get("no_merge_starts"),
        )
        if verbose:
            print(f"  After AI tuning: {q_ai}")
        rule["tuned_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rule["metrics"]  = {
            "median_len": q_ai.median_len,
            "short_frac": q_ai.short_frac,
            "n_segs":     q_ai.n_segs,
        }
        rules[stem] = rule
        return rule
    elif use_ai:
        if verbose:
            print("  ⚠️  Ollama not available — using best grid result")

    rule = {
        "gap_threshold":   best_gap,
        "merge_endings":   [],
        "no_merge_starts": [],
        "method":          "grid-best-effort",
        "tuned_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": {
            "median_len":  best_q.median_len,
            "short_frac":  best_q.short_frac,
            "n_segs":      best_q.n_segs,
        },
    }
    rules[stem] = rule
    return rule


# ─────────────────────────────────────────────────────────────────────────────
# Discover PDFs from corpus segment files
# ─────────────────────────────────────────────────────────────────────────────

def _find_pdf(stem: str) -> Path | None:
    norm_base = re.sub(r"v\d{6}p$", "", stem)
    for root in DOWNLOAD_ROOTS:
        for p in sorted(root.glob(f"{norm_base}*.pdf"), reverse=True):
            return p
    return None


def iter_pdf_corpus() -> Iterator[Path]:
    for seg_file in sorted(CORPUS_SEG_DIR.glob("*.segments.json")):
        try:
            data = json.loads(seg_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("source_kind", "pdf") != "pdf":
            continue
        pdf_path = Path(data.get("source_pdf", ""))
        if pdf_path.is_file():
            yield pdf_path
            continue
        stem = seg_file.stem.replace(".segments", "")
        found = _find_pdf(stem)
        if found:
            yield found


# ─────────────────────────────────────────────────────────────────────────────
# Stats-only mode
# ─────────────────────────────────────────────────────────────────────────────

def print_stats(rules: dict, verbose: bool = True) -> None:
    if not HAS_PDFPLUMBER:
        print("[ERROR] pdfplumber not installed", file=sys.stderr)
        return

    pdfs = list(iter_pdf_corpus())
    if not pdfs:
        print("No PDF corpus entries found.")
        return

    ok = bad = collapsed = 0
    for pdf in pdfs:
        stem  = pdf.stem
        rule  = rules.get(stem, {})
        gap   = rule.get("gap_threshold", 20)
        extra = rule.get("merge_endings", [])
        nostrt= rule.get("no_merge_starts", [])
        q = _measure_pdf(pdf, gap, extra, nostrt)
        print(q)
        if q.ok:
            ok += 1
        elif q.collapsed:
            collapsed += 1
        else:
            bad += 1

    print(f"\n  OK: {ok}   Collapsed: {collapsed}   Needs tuning: {bad}   Total: {ok+bad+collapsed}")


# ─────────────────────────────────────────────────────────────────────────────
# --apply: re-run pdf-segment.py with learned rules
# ─────────────────────────────────────────────────────────────────────────────

def apply_rules(verbose: bool = True) -> None:
    import subprocess
    rules = load_rules()
    if not rules:
        print("No learned rules found — run without --apply first.")
        return

    for stem, rule in rules.items():
        seg_file = CORPUS_SEG_DIR / f"{stem}.segments.json"
        if not seg_file.exists():
            continue
        try:
            data = json.loads(seg_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdf_path = Path(data.get("source_pdf", ""))
        if not pdf_path.is_file():
            pdf_path = _find_pdf(stem) or Path("")
        if not pdf_path.is_file():
            if verbose:
                print(f"  ⚠️  {stem}: PDF not found, skipping")
            continue

        cmd = [
            sys.executable, "scripts/pdf-segment.py",
            "--pdf", str(pdf_path),
            "--force",
        ]
        if verbose:
            print(f"  🔄  {stem}: re-segmenting with gap={rule['gap_threshold']}")
        try:
            subprocess.run(cmd, check=True, capture_output=not verbose)
        except subprocess.CalledProcessError as exc:
            print(f"  [ERROR] {stem}: {exc}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Integration hook for pdf-segment.py
# ─────────────────────────────────────────────────────────────────────────────

def get_rule_for_stem(stem: str) -> dict:
    """
    Public API for pdf-segment.py:

        from scripts.para_tune import get_rule_for_stem
        rule = get_rule_for_stem(stem)
        gap          = rule.get('gap_threshold', 20)
        extra_end    = rule.get('merge_endings', [])
        extra_nostrt = rule.get('no_merge_starts', [])
    """
    return load_rules().get(stem, {})


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adaptive paragraph-boundary calibrator for ETSI PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/para-tune.py                        # tune all corpus PDFs
  python3 scripts/para-tune.py --pdf downloads/specs/TS/ts_11914402v020101p.pdf
  python3 scripts/para-tune.py --no-ai                # grid search only
  python3 scripts/para-tune.py --model mistral        # use different Ollama model
  python3 scripts/para-tune.py --stats                # metrics only
  python3 scripts/para-tune.py --apply                # re-segment with learned rules
""",
    )
    parser.add_argument("--pdf",    metavar="PDF",  help="Tune a single PDF file")
    parser.add_argument("--no-ai",  action="store_true", help="Disable Ollama fallback")
    parser.add_argument("--model",  default=OLLAMA_MODEL, help=f"Ollama model (default: {OLLAMA_MODEL})")
    parser.add_argument("--stats",  action="store_true",  help="Print current metrics only")
    parser.add_argument("--apply",  action="store_true",  help="Re-segment corpus with learned rules")
    parser.add_argument("--quiet",  action="store_true",  help="Less output")
    args = parser.parse_args()

    if not HAS_PDFPLUMBER:
        sys.exit("[ERROR] pdfplumber not installed — run: pip install pdfplumber")

    rules   = load_rules()
    verbose = not args.quiet

    if args.apply:
        apply_rules(verbose=verbose)
        return

    if args.stats:
        print_stats(rules, verbose=verbose)
        return

    if args.pdf:
        pdfs = [Path(args.pdf)]
        if not pdfs[0].is_file():
            sys.exit(f"[ERROR] File not found: {pdfs[0]}")
    else:
        pdfs = list(iter_pdf_corpus())
        if not pdfs:
            sys.exit("[ERROR] No PDF corpus entries found. Run pdf-segment.py first.")

    if verbose:
        ai_status = "disabled" if args.no_ai else (
            f"enabled ({args.model})" if _ollama_available(args.model) else "not available"
        )
        print(f"[para-tune]  {len(pdfs)} PDF(s)  |  AI fallback: {ai_status}")
        print(f"             target: median ≥ {MIN_MEDIAN_CHARS}  short_frac ≤ {MAX_SHORT_FRAC*100:.0f}%  n ≥ {MIN_SEGS_ABSOLUTE}\n")

    changed = 0
    for pdf in pdfs:
        result = tune_pdf(
            pdf, rules,
            use_ai=not args.no_ai,
            model=args.model,
            verbose=verbose,
        )
        if result is not None:
            changed += 1
            save_rules(rules)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  Rules updated: {changed}")
        print(f"  Rules file   : {RULES_PATH}")
        print(f"  Next step    : python3 scripts/para-tune.py --apply")
        print(f"{'─'*60}")


if __name__ == "__main__":
    main()
