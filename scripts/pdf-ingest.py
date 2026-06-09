#!/usr/bin/env python3
"""
pdf-ingest.py  —  ETSI PDF  →  corpus/specs/<norm>_<version>.json

Usage:
    # Einen Unterordner:
    python3 scripts/pdf-ingest.py downloads/specs/EN/
    # Ganzer specs-Baum (alle Unterordner rekursiv):
    python3 scripts/pdf-ingest.py downloads/specs/
    # Einzeldatei:
    python3 scripts/pdf-ingest.py downloads/specs/EN/en_319403v020202p.pdf

Output pro Norm:
    corpus/specs/en_319403v020202p.json

JSON-Schema (pro Eintrag im Array "pages"):
    {
      "page_nr":       1,
      "anchor":        "#page=1",
      "section":       "4.2.1",
      "section_title": "General requirements",
      "text_clean":    "..."
    }

Top-Level:
    {
      "norm":        "EN 319 403",
      "version":     "v2.2.2",
      "source_pdf":  "downloads/specs/EN/en_319403v020202p.pdf",
      "ingested_at": "2026-06-10T00:00:00Z",
      "page_count":  42,
      "pages":       [ ... ]
    }
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
    sys.exit("[ERROR] pdfplumber nicht gefunden. Bitte: npm run setup:py")


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$",
    re.MULTILINE,
)

# ETSI FILENAME SCHEMAS:
#   Schema A (ETSI-Download): en_319403v020202p.pdf
#     → norm "EN 319 403", version "v2.2.2"
#   Schema B (manuell):       EN_319_401_v2.2.1.pdf
#     → norm "EN 319 401", version "v2.2.1"

# Schema A: en_<3digits><3digits>v<2><2><2>p.pdf  (ETSI Portal)
# Gruppe: type=en/ts/tr, family=319, number=403, v=020202
FILENAME_ETSI_RE = re.compile(
    r"^(?P<type>[a-z]+)_(?P<family>\d{3})(?P<number>\d{3,4})v(?P<vmaj>\d{2})(?P<vmin>\d{2})(?P<vpatch>\d{2})p\.pdf$",
    re.IGNORECASE,
)

# Schema B: EN_319_401_v2.2.1.pdf
FILENAME_MANUAL_RE = re.compile(
    r"^(?P<norm>[A-Z]+_\d+_\d+(?:_\d+)?)_(?P<version>v[\d.]+)\.pdf$",
    re.IGNORECASE,
)

# Dateien die uebersprungen werden sollen (Metadaten-Dateien des Scrapers)
SKIP_PREFIXES = (".",)  # .headers.*, .url.*


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def parse_filename(pdf_path: Path):
    """Liest Norm und Version aus dem Dateinamen.
    Unterstuetzt ETSI-Portal-Schema (en_319403v020202p.pdf)
    und manuelles Schema (EN_319_401_v2.2.1.pdf).
    Fallback: stem als Norm, 'vX.X.X' als Version.
    """
    name = pdf_path.name

    # ETSI-Portal-Schema
    m = FILENAME_ETSI_RE.match(name)
    if m:
        doc_type = m.group("type").upper()   # EN / TS / TR
        family   = m.group("family")          # 319
        number   = m.group("number")          # 403
        vmaj     = int(m.group("vmaj"))       # 02 → 2
        vmin     = int(m.group("vmin"))       # 02 → 2
        vpatch   = int(m.group("vpatch"))     # 02 → 2
        norm     = f"{doc_type} {family} {number}"
        version  = f"v{vmaj}.{vmin}.{vpatch}"
        return norm, version

    # Manuelles Schema
    m = FILENAME_MANUAL_RE.match(name)
    if m:
        norm = m.group("norm").replace("_", " ")
        return norm, m.group("version").lower()

    # Fallback
    return pdf_path.stem, "vX.X.X"


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def extract_sections_from_page(text: str):
    return [(m.group("num").rstrip("."), m.group("title").strip())
            for m in SECTION_RE.finditer(text)]


def output_path(pdf_path: Path, corpus_root: Path) -> Path:
    specs_dir = corpus_root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    return specs_dir / (pdf_path.stem + ".json")


def collect_pdfs(inputs: list[str]) -> list[Path]:
    """Sammelt alle echten PDFs aus Dateien und Ordnern (rekursiv).
    Ueberspringt versteckte Dateien wie .headers.* und .url.*
    """
    result: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            # Rekursiv alle .pdf suchen, versteckte Dateien ignorieren
            for pdf in sorted(p.rglob("*.pdf")):
                if not pdf.name.startswith(SKIP_PREFIXES):
                    result.append(pdf)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            if not p.name.startswith(SKIP_PREFIXES):
                result.append(p)
        else:
            print(f"[WARN]  {inp} ist kein PDF und kein Ordner — uebersprungen.",
                  file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Kern: ein PDF einlesen
# ---------------------------------------------------------------------------

def ingest_pdf(pdf_path: Path, corpus_root: Path, verbose: bool = True) -> dict:
    norm, version = parse_filename(pdf_path)
    if verbose:
        print(f"[INGEST] {pdf_path.name}  →  {norm}  {version}")

    pages_out = []
    current_section = ""
    current_title   = ""

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            page_nr = page.page_number
            raw  = page.extract_text(layout=False) or ""
            text = clean_text(raw)

            hits = extract_sections_from_page(text)
            if hits:
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
        char_count = sum(len(p["text_clean"]) for p in pages_out)
        print(f"[OK]     {out}  ({page_count} Seiten, {char_count:,} Zeichen)")
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ETSI PDF  →  corpus/specs/*.json",
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="PDF-Datei(en) oder Ordner (rekursiv). Beispiel: downloads/specs/",
    )
    parser.add_argument(
        "--corpus",
        default="corpus",
        help="Corpus-Wurzel (Standard: corpus/)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Weniger Ausgabe",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="JSON-Dateien die bereits existieren ueberspringen",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus)
    verbose = not args.quiet

    pdf_files = collect_pdfs(args.input)

    if not pdf_files:
        sys.exit("[ERROR] Keine PDF-Dateien gefunden.")

    if verbose:
        print(f"[INFO]  {len(pdf_files)} PDFs gefunden.")

    ok = skipped = errors = 0
    for pdf in pdf_files:
        out = output_path(pdf, corpus_root)
        if args.skip_existing and out.exists():
            if verbose:
                print(f"[SKIP]  {pdf.name} (bereits vorhanden)")
            skipped += 1
            continue
        try:
            ingest_pdf(pdf, corpus_root, verbose=verbose)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {pdf.name}: {exc}", file=sys.stderr)
            errors += 1

    if verbose:
        print(f"\n[DONE]  {ok} verarbeitet, {skipped} uebersprungen, {errors} Fehler "
              f"(von {len(pdf_files)} gesamt).")


if __name__ == "__main__":
    main()
