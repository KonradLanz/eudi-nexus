#!/usr/bin/env python3
"""
spec-search.py  —  Volltext-Suche über corpus/specs/*.json

Usage:
    python3 scripts/spec-search.py "revocation"           # alle Normen
    python3 scripts/spec-search.py "audit" --norm 401     # nur EN 319 401
    python3 scripts/spec-search.py "key management" --json  # JSON-Ausgabe
    python3 scripts/spec-search.py "TSP" --context 3       # 3 Zeilen Kontext

Output (Standard):
    [EN 319 401 v2.2.1]  § 4.2.1 General requirements  — #page=7
    ...text snippet mit Treffer...

Abhängigkeiten: keine (nur stdlib)
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

def search_file(json_path: Path, pattern: re.Pattern, context_lines: int) -> list[dict]:
    """Durchsucht eine corpus/specs/*.json nach dem Muster.
    Gibt Liste von Treffern zurück.
    """
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] {json_path.name}: {exc}", file=sys.stderr)
        return []

    norm    = doc.get("norm", "?")
    version = doc.get("version", "?")
    hits    = []

    for page in doc.get("pages", []):
        text = page.get("text_clean", "")
        if not pattern.search(text):
            continue

        snippet = _snippet(text, pattern, context_lines)
        hits.append({
            "norm":          norm,
            "version":       version,
            "page_nr":       page["page_nr"],
            "anchor":        page["anchor"],
            "section":       page.get("section", ""),
            "section_title": page.get("section_title", ""),
            "snippet":       snippet,
            "source_file":   str(json_path),
        })

    return hits


def _snippet(text: str, pattern: re.Pattern, context_lines: int) -> str:
    """Gibt die Zeilen rund um den ersten Treffer zurück."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if pattern.search(line):
            start = max(0, i - context_lines)
            end   = min(len(lines), i + context_lines + 1)
            chunk = lines[start:end]
            # Treffer-Zeile markieren
            chunk[i - start] = ">>> " + chunk[i - start]
            return "\n".join(chunk)
    return ""


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

def print_hits(hits: list[dict], query: str):
    if not hits:
        print(f"Keine Treffer für {query!r}.")
        return

    print(f"\n{len(hits)} Treffer für {query!r}\n" + "─" * 60)
    for h in hits:
        section_info = ""
        if h["section"]:
            section_info = f"  \u00a7 {h['section']} {h['section_title']}"
        print(f"\n[{h['norm']} {h['version']}]{section_info}  —  {h['anchor']}")
        if h["snippet"]:
            for line in h["snippet"].splitlines():
                print("    " + line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Volltext-Suche über corpus/specs/*.json",
    )
    parser.add_argument("query", help="Suchbegriff (Regex unterstützt)")
    parser.add_argument(
        "--corpus", default="corpus",
        help="Corpus-Wurzel (Standard: corpus/)",
    )
    parser.add_argument(
        "--norm", default=None,
        help="Nur Normen deren Dateiname diesen String enthält (z.B. '401')",
    )
    parser.add_argument(
        "--context", type=int, default=2,
        help="Anzahl Kontextzeilen um den Treffer (Standard: 2)",
    )
    parser.add_argument(
        "--ignore-case", action="store_true", default=True,
        help="Gross-/Kleinschreibung ignorieren (Standard: an)",
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true",
        help="Ausgabe als JSON-Array statt lesbarem Text",
    )
    args = parser.parse_args()

    flags   = re.IGNORECASE if args.ignore_case else 0
    try:
        pattern = re.compile(args.query, flags)
    except re.error as exc:
        sys.exit(f"[ERROR] Ungültiger Regex {args.query!r}: {exc}")

    specs_dir = Path(args.corpus) / "specs"
    if not specs_dir.exists():
        sys.exit(f"[ERROR] {specs_dir} nicht gefunden. Bitte zuerst pdf-ingest.py ausführen.")

    json_files = sorted(specs_dir.glob("*.json"))
    if args.norm:
        json_files = [f for f in json_files if args.norm in f.name]

    if not json_files:
        sys.exit("[ERROR] Keine JSON-Dateien gefunden (Norm-Filter zu eng?).")

    all_hits: list[dict] = []
    for jf in json_files:
        all_hits.extend(search_file(jf, pattern, args.context))

    if args.json_out:
        print(json.dumps(all_hits, ensure_ascii=False, indent=2))
    else:
        print_hits(all_hits, args.query)


if __name__ == "__main__":
    main()
