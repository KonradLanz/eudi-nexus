#!/usr/bin/env python3
"""
skip-review.py  —  Analyse skipped segment types in the corpus

For every .segments.json file, collects all segments whose type would be
skipped by build-index.py (HEADER, FOOTER, TOC, OTHER, TABLE, SECTION, …)
and writes a review report to corpus/specs/_review/skip-review.json.

Also runs a "variable content" detector: if the same segment type has
high text variance across documents it may contain indexable content
(e.g. TABLE rows with requirements), whereas purely static boilerplate
(copyright footers, TOC page numbers) is low-variance.

Usage:
  python3 scripts/skip-review.py
  python3 scripts/skip-review.py --types OTHER TABLE        # focus on specific types
  python3 scripts/skip-review.py --out corpus/skip.json     # custom output path
  python3 scripts/skip-review.py --show-samples 5           # more text samples

Output (corpus/specs/_review/skip-review.json):
  {
    "summary": { <type>: { count, files, avg_len, sample_texts, verdict } },
    "per_file": { <stem>: [ { id, type, text, page, section } ] },
    "dedup": [ { text, count, files } ]   # exact-duplicate texts across files
  }
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

SEGMENTS_DIR = Path("corpus/specs/_segments")
DEFAULT_OUT  = Path("corpus/specs/_review/skip-review.json")

# Types that build-index.py currently skips
SKIP_TYPES     = {"HEADER", "FOOTER", "TOC", "OTHER"}
FTS_ONLY_TYPES = {"SECTION"}
EMBED_TYPES    = {"NORM", "INFORM"}

# Heuristic: if normalised text length CV > this, content is "variable"
CV_THRESHOLD = 0.40

# Patterns that suggest purely boilerplate / static content
BOILERPLATE_PATTERNS = [
    re.compile(r"^\d+$"),                          # page number only
    re.compile(r"^ETSI\s", re.I),                  # ETSI header
    re.compile(r"Draft\s+EN", re.I),
    re.compile(r"^Table of [Cc]ontents"),
    re.compile(r"^[Cc]opyright"),
    re.compile(r"^\.\.+\s*\d+$"),                  # TOC dotleader + page
    re.compile(r"^\s{0,4}[IVX]+\s{0,4}$"),        # Roman numeral alone
]


def _is_boilerplate(text: str) -> bool:
    t = text.strip()
    if len(t) < 5:
        return True
    for pat in BOILERPLATE_PATTERNS:
        if pat.search(t):
            return True
    return False


def _normalise(text: str) -> str:
    """Replace numbers/dates/codes with placeholders for variability test."""
    t = text.strip()
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>", t)
    t = re.sub(r"\b\d+\b", "<N>", t)
    t = re.sub(r"v\d+\.\d+\.\d+", "<VER>", t, flags=re.I)
    t = re.sub(r"\s+", " ", t)
    return t


def analyse(
    segments_dir: Path,
    focus_types: set[str] | None,
    n_samples: int,
) -> dict:
    # type → list of (text, file_stem, id, page, section)
    by_type: dict[str, list[tuple]] = defaultdict(list)
    per_file: dict[str, list[dict]] = {}

    for f in sorted(segments_dir.glob("*.segments.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        stem = f.stem
        skipped_in_file: list[dict] = []

        for seg in data.get("segments", []):
            seg_type = seg.get("type", "OTHER")
            if seg_type in EMBED_TYPES:
                continue  # indexed — not our concern
            if focus_types and seg_type not in focus_types:
                continue
            text = seg.get("text", "").strip()
            entry = {
                "id":       seg.get("id", ""),
                "type":     seg_type,
                "text":     text,
                "page":     seg.get("page"),
                "section":  seg.get("section", ""),
                "boiler":   _is_boilerplate(text),
            }
            skipped_in_file.append(entry)
            by_type[seg_type].append((text, stem, seg.get("id", ""),
                                      seg.get("page"), seg.get("section", "")))

        if skipped_in_file:
            per_file[stem] = skipped_in_file

    # ── Per-type summary ──
    summary: dict[str, dict] = {}
    for seg_type, items in sorted(by_type.items()):
        texts  = [t for t, *_ in items]
        files  = sorted({f for _, f, *_ in items})
        lengths = [len(t) for t in texts]
        non_boiler = [t for t in texts if not _is_boilerplate(t)]
        boiler_pct = (len(texts) - len(non_boiler)) / max(len(texts), 1) * 100

        # Variability: normalise texts, compute coefficient of variation of length
        normed_lens = [len(_normalise(t)) for t in texts if t.strip()]
        if len(normed_lens) > 1:
            cv = statistics.stdev(normed_lens) / max(statistics.mean(normed_lens), 1)
        else:
            cv = 0.0

        # Dedup non-boilerplate sample texts
        seen: set[str] = set()
        samples: list[str] = []
        for t in non_boiler:
            key = t[:200]
            if key not in seen:
                seen.add(key)
                samples.append(t[:300])
            if len(samples) >= n_samples:
                break

        # Verdict
        if boiler_pct > 80:
            verdict = "static"           # almost entirely boilerplate → keep skipping
        elif cv > CV_THRESHOLD and non_boiler:
            verdict = "review"           # high variance → may contain indexable content
        elif non_boiler:
            verdict = "low-variance"     # some non-boilerplate but repetitive
        else:
            verdict = "static"

        summary[seg_type] = {
            "count":         len(items),
            "files":         len(files),
            "avg_len":       round(statistics.mean(lengths), 1) if lengths else 0,
            "boilerplate_pct": round(boiler_pct, 1),
            "content_cv":    round(cv, 3),
            "verdict":       verdict,
            "action":        (
                "Consider indexing as FTS-only (SECTION-style)"
                if verdict == "review" else
                "Keep skipping"
            ),
            "samples":       samples,
        }

    # ── Exact-duplicate detection across files ──
    hash_to_entries: dict[str, list[dict]] = defaultdict(list)
    for seg_type, items in by_type.items():
        for text, stem, seg_id, page, section in items:
            if _is_boilerplate(text):
                continue
            h = hashlib.md5(text.encode()).hexdigest()
            hash_to_entries[h].append({
                "file": stem, "id": seg_id, "type": seg_type, "text": text[:200]
            })

    dedup = [
        {"text": entries[0]["text"], "count": len(entries),
         "files": sorted({e["file"] for e in entries}),
         "type": entries[0]["type"]}
        for h, entries in hash_to_entries.items()
        if len({e["file"] for e in entries}) > 1  # only cross-file dupes
    ]
    dedup.sort(key=lambda x: -x["count"])

    return {"summary": summary, "per_file": per_file, "dedup": dedup[:200]}


def print_table(summary: dict) -> None:
    print(f"\n{'Type':<12} {'Count':>7} {'Files':>6} {'AvgLen':>7} "
          f"{'Boiler%':>8} {'CV':>6}  Verdict")
    print("-" * 72)
    for t, s in sorted(summary.items(), key=lambda x: -x[1]["count"]):
        verdict = s["verdict"]
        flag = "🔍" if verdict == "review" else "✓ " if verdict == "static" else "~"
        print(
            f"{t:<12} {s['count']:>7,} {s['files']:>6} "
            f"{s['avg_len']:>7.0f} {s['boilerplate_pct']:>7.1f}% "
            f"{s['content_cv']:>6.3f}  {flag} {verdict}"
        )
        if verdict == "review" and s["samples"]:
            for samp in s["samples"][:2]:
                print(f"  → {samp[:120]!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse skipped segments in the corpus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--segments",    default=str(SEGMENTS_DIR))
    parser.add_argument("--out",         default=str(DEFAULT_OUT))
    parser.add_argument("--types",       nargs="+", default=None,
                        help="Focus on specific types, e.g. OTHER TABLE")
    parser.add_argument("--show-samples", type=int, default=3)
    parser.add_argument("--no-write",    action="store_true",
                        help="Print summary but do not write JSON")
    args = parser.parse_args()

    segments_dir = Path(args.segments)
    out_path     = Path(args.out)
    focus_types  = set(args.types) if args.types else None

    if not segments_dir.is_dir():
        print(f"[ERROR] Segments dir not found: {segments_dir}")
        raise SystemExit(1)

    print(f"Scanning {segments_dir} …")
    report = analyse(segments_dir, focus_types, args.show_samples)

    print_table(report["summary"])

    dedup = report["dedup"]
    if dedup:
        print(f"\nCross-file duplicates (non-boilerplate): {len(dedup)}")
        for d in dedup[:5]:
            print(f"  [{d['type']}] ×{d['count']} in {len(d['files'])} files: {d['text'][:80]!r}")

    if not args.no_write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport written → {out_path}")


if __name__ == "__main__":
    main()
