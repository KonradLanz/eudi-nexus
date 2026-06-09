#!/usr/bin/env python3
"""
ingest-qa.py  —  Qualitaetspruefung der ingested corpus/specs/*.json

Usage:
    npm run qa                    # alle JSONs pruefen
    npm run qa -- --verbose       # + Detailausgabe pro Datei
    npm run qa -- --norm CAdES    # nur eine Norm pruefen
    npm run qa -- --json          # maschinenlesbare Zusammenfassung

Prueft:
    1. Kurzname-Abdeckung     (shortname gesetzt?)
    2. Text-Coverage          (leere Seiten?)
    3. Section-Parsing        (mindestens eine Abschnittsnummer erkannt?)
    4. Norm-Parsing           (kein Fallback 'vX.X.X'?)
    5. Duplikate              (gleiche norm+version mehrfach ingested?)
    6. Mindest-Seitenanzahl   (< 3 Seiten = wahrscheinlich korrupt)
    7. Zeichendichte          (< 100 Zeichen/Seite = scanned/leer)

Exit-Code:
    0 = alles OK
    1 = Warnungen (non-blocking)
    2 = Fehler (blocking)
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Schwellwerte
# ---------------------------------------------------------------------------
MIN_PAGES          = 3       # < 3 Seiten: wahrscheinlich korrupt
MIN_CHARS_PER_PAGE = 100     # < 100 Zeichen/Seite: scanned oder leer
MIN_SECTION_RATIO  = 0.10    # mind. 10% der Seiten haben eine Abschnittsnummer


# ---------------------------------------------------------------------------
# ANSI-Farben (werden bei --json deaktiviert)
# ---------------------------------------------------------------------------
class C:
    OK   = "\033[32m"
    WARN = "\033[33m"
    ERR  = "\033[31m"
    BOLD = "\033[1m"
    DIM  = "\033[2m"
    RST  = "\033[0m"

    @classmethod
    def disable(cls):
        cls.OK = cls.WARN = cls.ERR = cls.BOLD = cls.DIM = cls.RST = ""


# ---------------------------------------------------------------------------
# Einzeldatei pruefen
# ---------------------------------------------------------------------------

def check_file(path: Path, verbose: bool = False) -> dict:
    """Gibt ein dict mit 'errors', 'warnings', 'info' zurueck."""
    result = {
        "file":     path.name,
        "norm":     "",
        "shortname": "",
        "version":  "",
        "pages":    0,
        "errors":   [],
        "warnings": [],
        "info":     [],
    }

    # --- JSON laden ---
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        result["errors"].append(f"JSON nicht lesbar: {e}")
        return result

    norm      = doc.get("norm", "")
    shortname = doc.get("shortname", "")
    version   = doc.get("version", "")
    pages     = doc.get("pages", [])
    page_count = doc.get("page_count", 0)

    result["norm"]      = norm
    result["shortname"] = shortname
    result["version"]   = version
    result["pages"]     = page_count

    # --- Check 1: Norm-Parsing ---
    if version == "vX.X.X":
        result["warnings"].append("Version konnte nicht aus Dateiname gelesen werden (Fallback vX.X.X)")
    if not norm or norm == path.stem:
        result["warnings"].append("Norm konnte nicht aus Dateiname geparst werden")

    # --- Check 2: Kurzname ---
    if not shortname:
        result["warnings"].append("Kein Kurzname (shortname) — evtl. in SHORTNAMES-Dict nachtragen")
    else:
        result["info"].append(f"Kurzname: {shortname}")

    # --- Check 3: Mindest-Seiten ---
    if page_count < MIN_PAGES:
        result["errors"].append(f"Nur {page_count} Seite(n) — wahrscheinlich korrupt oder leer")

    # --- Check 4: Text-Coverage ---
    if pages:
        empty_pages = [p["page_nr"] for p in pages if len(p.get("text_clean", "")) < 50]
        empty_ratio = len(empty_pages) / len(pages)
        if empty_ratio > 0.3:
            result["warnings"].append(
                f"{len(empty_pages)}/{len(pages)} Seiten haben < 50 Zeichen "
                f"({empty_ratio:.0%}) — möglicherweise gescanntes PDF"
            )
        elif empty_pages and verbose:
            result["info"].append(f"Seiten mit wenig Text: {empty_pages[:5]}{'...' if len(empty_pages)>5 else ''}")

        # Durchschnittliche Zeichendichte
        avg_chars = sum(len(p.get("text_clean", "")) for p in pages) / len(pages)
        if avg_chars < MIN_CHARS_PER_PAGE:
            result["warnings"].append(
                f"Durchschnittlich nur {avg_chars:.0f} Zeichen/Seite — "
                f"möglicherweise gescanntes oder leeres PDF"
            )
        else:
            result["info"].append(f"Zeichendichte: ø {avg_chars:.0f} Zeichen/Seite")

    # --- Check 5: Section-Parsing ---
    if pages:
        pages_with_section = sum(
            1 for p in pages if p.get("section", "").strip()
        )
        ratio = pages_with_section / len(pages)
        if ratio < MIN_SECTION_RATIO:
            result["warnings"].append(
                f"Section-Erkennung schwach: nur {pages_with_section}/{len(pages)} Seiten "
                f"haben eine Abschnittsnummer ({ratio:.0%}) — evtl. SECTION_RE anpassen"
            )
        else:
            result["info"].append(
                f"Section-Erkennung: {pages_with_section}/{len(pages)} Seiten ({ratio:.0%})"
            )

    return result


# ---------------------------------------------------------------------------
# Duplikat-Pruefung
# ---------------------------------------------------------------------------

def check_duplicates(results: list[dict]) -> list[str]:
    seen: defaultdict[str, list[str]] = defaultdict(list)
    for r in results:
        if r["norm"] and r["version"]:
            key = f"{r['norm']}|{r['version']}"
            seen[key].append(r["file"])
    return [
        f"{norm_ver.replace('|', ' ')} erscheint {len(files)}x: {', '.join(files)}"
        for norm_ver, files in seen.items()
        if len(files) > 1
    ]


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

def print_result(r: dict, verbose: bool) -> None:
    icon = C.OK + "✅" + C.RST if not r["errors"] and not r["warnings"] else ""
    if r["errors"]:
        icon = C.ERR + "❌" + C.RST
    elif r["warnings"]:
        icon = C.WARN + "⚠️" + C.RST

    name_part = f"{C.BOLD}{r['norm'] or r['file']}{C.RST}"
    short_part = f"  {C.DIM}[{r['shortname']}]{C.RST}" if r["shortname"] else ""
    ver_part   = f"  {C.DIM}{r['version']}{C.RST}" if r["version"] else ""
    page_part  = f"  {C.DIM}{r['pages']}S{C.RST}" if r["pages"] else ""

    print(f"  {icon}  {name_part}{short_part}{ver_part}{page_part}")

    for e in r["errors"]:
        print(f"       {C.ERR}ERROR{C.RST}  {e}")
    for w in r["warnings"]:
        print(f"       {C.WARN}WARN {C.RST}  {w}")
    if verbose:
        for i in r["info"]:
            print(f"       {C.DIM}INFO   {i}{C.RST}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qualitaetspruefung der ingested corpus/specs/*.json",
    )
    parser.add_argument(
        "--corpus", default="corpus",
        help="Corpus-Wurzel (Standard: corpus/)",
    )
    parser.add_argument(
        "--norm", "-n", default=None,
        help="Nur Dateien pruefen deren Norm/Kurzname diesen String enthaelt (case-insensitive)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Info-Zeilen pro Datei ausgeben",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Maschinenlesbare JSON-Zusammenfassung ausgeben",
    )
    args = parser.parse_args()

    if args.json:
        C.disable()

    specs_dir = Path(args.corpus) / "specs"
    if not specs_dir.exists():
        sys.exit(f"[ERROR] {specs_dir} nicht gefunden. Zuerst: npm run ingest")

    json_files = sorted(f for f in specs_dir.glob("*.json") if f.name != ".gitkeep")
    if not json_files:
        sys.exit("[ERROR] Keine JSON-Dateien in corpus/specs/ — zuerst: npm run ingest")

    # Filter
    if args.norm:
        needle = args.norm.lower()
        json_files = [
            f for f in json_files
            if needle in f.stem.lower()
        ]
        if not json_files:
            sys.exit(f"[ERROR] Keine Dateien gefunden die '{args.norm}' enthalten")

    if not args.json:
        print(f"\n{C.BOLD}EUDI-Nexus Ingest QA{C.RST}  —  {len(json_files)} Dateien\n")

    results = [check_file(f, verbose=args.verbose) for f in json_files]

    # Duplikate
    dup_warnings = check_duplicates(results)

    # Ausgabe
    if args.json:
        summary = {
            "total":    len(results),
            "ok":       sum(1 for r in results if not r["errors"] and not r["warnings"]),
            "warnings": sum(1 for r in results if r["warnings"] and not r["errors"]),
            "errors":   sum(1 for r in results if r["errors"]),
            "no_shortname": sum(1 for r in results if not r["shortname"]),
            "duplicates":   len(dup_warnings),
            "files":    results,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.exit(0 if summary["errors"] == 0 else 2)

    for r in results:
        print_result(r, verbose=args.verbose)

    # Duplikat-Sektion
    if dup_warnings:
        print(f"\n{C.WARN}Duplikate:{C.RST}")
        for d in dup_warnings:
            print(f"  {C.WARN}⚠️{C.RST}  {d}")

    # Zusammenfassung
    n_ok   = sum(1 for r in results if not r["errors"] and not r["warnings"])
    n_warn = sum(1 for r in results if r["warnings"] and not r["errors"])
    n_err  = sum(1 for r in results if r["errors"])
    n_no_short = sum(1 for r in results if not r["shortname"])

    print(f"\n{'─'*60}")
    print(f"  {C.OK}✅ OK{C.RST}       {n_ok:>4}")
    print(f"  {C.WARN}⚠️  Warnungen{C.RST} {n_warn:>4}")
    print(f"  {C.ERR}❌ Fehler{C.RST}   {n_err:>4}")
    if n_no_short:
        print(f"  {C.DIM}❔ Ohne Kurzname{C.RST} {n_no_short:>4}  →  in scripts/pdf-ingest.py SHORTNAMES ergänzen")
    if dup_warnings:
        print(f"  {C.WARN}🔄 Duplikate{C.RST}  {len(dup_warnings):>4}")
    print()

    sys.exit(2 if n_err > 0 else (1 if n_warn > 0 else 0))


if __name__ == "__main__":
    main()
