#!/usr/bin/env python3
"""
test_merge_sections.py  —  Unit test für _merge_short_segments() section-boundary-Regeln

Regeln:
  1. section='' + section=''   → darf mergen (anonyme Zone)
  2. section='5.1' + '5.1'    → darf mergen (gleiche Clause)
  3. '' → '5.1'               → flush (Section wechselt)
  4. '5.1' → '5.2'            → flush (Clause-Grenze)
  5. NORM + INFORM selbe sec  → flush (type mismatch)
  6. SECTION-Tag              → immer flush
  7. MERGE_MAX_CHARS überschr.→ flush (hard cap)

Usage:
  python3 scripts/test_merge_sections.py
  python3 scripts/test_merge_sections.py -v
"""
from __future__ import annotations
import sys, pathlib, importlib

# Support both 'build_index.py' and 'build-index.py' filenames
try:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from scripts.build_index import _merge_short_segments, MERGE_MAX_CHARS
except ModuleNotFoundError:
    _spec = importlib.util.spec_from_file_location(
        "build_index",
        pathlib.Path(__file__).parent / "build-index.py",
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _merge_short_segments = _mod._merge_short_segments
    MERGE_MAX_CHARS = _mod.MERGE_MAX_CHARS

PASS = "\033[32m\u2713\033[0m"
FAIL = "\033[31m\u2717\033[0m"

SHORT = "The system shall comply with all listed requirements."  # ~53 chars


def seg(id: str, text: str, type: str = "NORM", section: str = "") -> dict:
    return {
        "id": id, "type": type, "page": 1, "anchor": "#p1",
        "section": section, "section_title": "", "text": text,
        "normative_keywords": ["shall"] if type == "NORM" else [],
    }


CASES: list[tuple[str, list[dict], list[str]]] = [
    (
        "1. '' + '' → merged (anonymous zone)",
        [seg("a", SHORT, section=""), seg("b", SHORT, section="")],
        ["a"],
    ),
    (
        "2. '5.1' + '5.1' → merged (same clause)",
        [seg("a", SHORT, section="5.1"), seg("b", SHORT, section="5.1")],
        ["a"],
    ),
    (
        "3. '' → '5.1' → flush (section change)",
        [seg("a", SHORT, section=""), seg("b", SHORT, section="5.1")],
        ["a", "b"],
    ),
    (
        "4. '5.1' → '5.2' → flush (clause boundary)",
        [seg("a", SHORT, section="5.1"), seg("b", SHORT, section="5.2")],
        ["a", "b"],
    ),
    (
        "5. NORM + INFORM same section → flush (type mismatch)",
        [
            seg("a", SHORT, type="NORM",   section="5.1"),
            seg("b", SHORT, type="INFORM", section="5.1"),
        ],
        ["a", "b"],
    ),
    (
        "6. SECTION tag → hard flush boundary",
        [
            seg("a", SHORT, section="5"),
            {"id": "hdr", "type": "SECTION", "page": 1, "anchor": "#p2",
             "section": "5.1", "section_title": "Reqs",
             "text": "5.1 Requirements", "normative_keywords": []},
            seg("c", SHORT, section="5.1"),
        ],
        ["a", "hdr", "c"],
    ),
    (
        "7. '' + '' + '' → all merged into first id",
        [
            seg("a", SHORT, section=""),
            seg("b", SHORT, section=""),
            seg("c", SHORT, section=""),
        ],
        ["a"],
    ),
    (
        "8. MERGE_MAX_CHARS cap → flush mid-section",
        [
            seg("a", "x" * (MERGE_MAX_CHARS - 10), section="5.1"),
            seg("b", "y" * 20, section="5.1"),
        ],
        ["a", "b"],
    ),
    (
        "9. '' '' '5.1' '5.1' '5.2' → full sequence",
        [
            seg("pre1", SHORT, section=""),
            seg("pre2", SHORT, section=""),
            seg("s1a",  SHORT, section="5.1"),
            seg("s1b",  SHORT, section="5.1"),
            seg("s2a",  SHORT, section="5.2"),
        ],
        ["pre1", "s1a", "s2a"],
    ),
]


def run_tests(verbose: bool = False) -> int:
    failures = 0
    for desc, inputs, expected_ids in CASES:
        result  = _merge_short_segments(inputs)
        got_ids = [s["id"] for s in result
                   if s.get("type") in ("NORM", "INFORM", "SECTION")]
        ok = got_ids == expected_ids
        print(f"  {PASS if ok else FAIL}  {desc}")
        if not ok or verbose:
            print(f"       expected : {expected_ids}")
            print(f"       got      : {got_ids}")
            if not ok:
                for s in result:
                    if s.get("type") in ("NORM", "INFORM", "SECTION"):
                        print(
                            f"         [{s['id']}] sec={s.get('section')!r} "
                            f"len={len(s['text'])} text={s['text'][:60]!r}"
                        )
        if not ok:
            failures += 1
    return failures


if __name__ == "__main__":
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    print(f"\nMerge section-boundary tests  (MERGE_MAX_CHARS={MERGE_MAX_CHARS})\n")
    failures = run_tests(verbose=verbose)
    total  = len(CASES)
    passed = total - failures
    print(f"\n{'\u2500' * 55}")
    color = "\033[32m" if not failures else "\033[31m"
    print(f"  {color}{passed}/{total} passed\033[0m")
    print()
    sys.exit(0 if not failures else 1)
