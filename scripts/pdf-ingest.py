#!/usr/bin/env python3
"""
pdf-ingest.py  —  ETSI EN 319 xxx PDF  →  corpus/specs/<norm>_<version>.json

Usage:
    python3 scripts/pdf-ingest.py downloads/specs/EN_319_401_v2.2.1.pdf
    python3 scripts/pdf-ingest.py downloads/specs/  # batch: alle PDFs im Ordner

Output pro Norm:
    corpus/specs/EN_319_401_v2.2.1.json

JSON-Schema (pro Eintrag im Array "pages"):
    {
      "page_nr":    1,                           # 1-basiert, wie im PDF-Viewer
      "anchor":     "#page=1",                    # kanonischer Deep-Link
      "section":    "4.2.1",                      # letzte bekannte Abschnittsnummer
      "section_title": "General requirements",   # Abschnittstitel
      "text_clean": "..."                         # Seitentext, bereinigt
    }

Ausserdem im Top-Level:
    {
      "norm":       "EN 319 401",
      "version":    "v2.2.1",
      "source_pdf": "downloads/specs/EN_319_401_v2.2.1.pdf",
      "ingested_at": "2026-06-10T00:45:00Z",
      "page_count": 42,
      "pages":      [ ... ]
    }

Abhängigkeiten:
    pip install pdfplumber
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("[ERROR] pdfplumber nicht gefunden. Bitte: pip install pdfplumber")


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# ETSI-Abschnittsüberschriften:
#   "4.2.1 General requirements"  oder  "4.2.1\tGeneral requirements"
#   Auch einstellig: "4 Scope", "A.1 Annex title"
SECTION_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$",
    re.MULTILINE,
)

# Dateiname-Schema: EN_319_401_v2.2.1.pdf  →  norm="EN 319 401", version="v2.2.1"
FILENAME_RE = re.compile(
    r"^(?P<norm>[A-Z]+_\d+_\d+(?:_\d+)?)_(?P<version>v[\d.]+)\.pdf$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def normalise_norm(raw: str) -> str:
    """EN_319_401  →  EN 319 401"""
    return raw.replace("_", " ")


def clean_text(raw: str) -> str:
    """Bereinigt Rohtext einer PDF-Seite:
    - Mehrfache Leerzeilen → eine
    - Steuerzeichen entfernen
    - Trailing whitespace entfernen
    """
    if not raw:
        return ""
    # Steuerzeichen ausser \n und \t entfernen
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", raw)
    # Mehrfache Leerzeilen verdichten
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Zeilenenden normalisieren
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def extract_sections_from_page(text: str):
    """Gibt Liste von (section_num, section_title) zurück, die auf dieser Seite beginnen."""
    return [(m.group("num").rstrip("."), m.group("title").strip())
            for m in SECTION_RE.finditer(text)]


def parse_filename(pdf_path: Path):
    """Versucht Norm und Version aus dem Dateinamen zu lesen.
    Fallback: ('UNKNOWN', 'vX.X.X')
    """
    m = FILENAME_RE.match(pdf_path.name)
    if m:
        return normalise_norm(m.group("norm")), m.group("version").lower()
    # Fallback: Dateiname ohne Extension, Version unbekannt
    stem = pdf_path.stem
    return normalise_norm(stem), "vX.X.X"


def output_path(pdf_path: Path, corpus_root: Path) -> Path:
    """Berechnet Ausgabepfad: corpus/specs/<stem>.json"""
    specs_dir = corpus_root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    return specs_dir / (pdf_path.stem + ".json")


# ---------------------------------------------------------------------------
# Kern: ein PDF einlesen
# ---------------------------------------------------------------------------

def ingest_pdf(pdf_path: Path, corpus_root: Path, verbose: bool = True) -> dict:
    norm, version = parse_filename(pdf_path)
    if verbose:
        print(f"[INGEST] {pdf_path.name}  →  norm={norm!r}  version={version}")

    pages_out = []
    current_section = ""       # letzte bekannte Abschnittsnummer
    current_title   = ""       # zugehöriger Titel

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            page_nr = page.page_number   # pdfplumber: 1-basiert

            # Text extrahieren (layout=False erhält Zeilenstruktur besser)
            raw = page.extract_text(layout=False) or ""
            text = clean_text(raw)

            # Neue Abschnitte auf dieser Seite?
            hits = extract_sections_from_page(text)
            if hits:
                # Letzter Treffer als aktiver Abschnitt
                current_section, current_title = hits[-1]

            pages_out.append({
                "page_nr":       page_nr,
                "anchor":        f"#page={page_nr}",
                "section":       current_section,
                "section_title": current_title,
                "text_clean":    text,
            })

            if verbose and page_nr % 20 == 0:
                print(f"  ... Seite {page_nr}/{page_count}")

    doc = {
        "norm":        norm,
        "version":     version,
        "source_pdf":  str(pdf_path),
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page_count":  page_count,
        "pages":       pages_out,
    }

    out = output_path(pdf_path, corpus_root)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print(f"[OK]    {out}  ({page_count} Seiten, "
              f"{sum(len(p['text_clean']) for p in pages_out):,} Zeichen)")
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ETSI EN 319 xxx PDF  →  corpus/specs/*.json",
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="PDF-Datei(en) oder Ordner mit PDFs (downloads/specs/)",
    )
    parser.add_argument(
        "--corpus",
        default="corpus",
        help="Wurzelverzeichnis des Corpus (Standard: corpus/)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Weniger Ausgabe",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus)
    verbose = not args.quiet

    # Alle PDFs sammeln
    pdf_files: list[Path] = []
    for inp in args.input:
        p = Path(inp)
        if p.is_dir():
            pdf_files.extend(sorted(p.glob("*.pdf")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdf_files.append(p)
        else:
            print(f"[WARN]  {inp} ist kein PDF und kein Ordner — übersprungen.",
                  file=sys.stderr)

    if not pdf_files:
        sys.exit("[ERROR] Keine PDF-Dateien gefunden.")

    ok = 0
    for pdf in pdf_files:
        try:
            ingest_pdf(pdf, corpus_root, verbose=verbose)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {pdf.name}: {exc}", file=sys.stderr)

    if verbose:
        print(f"\n[DONE]  {ok}/{len(pdf_files)} PDFs erfolgreich verarbeitet.")


if __name__ == "__main__":
    main()
