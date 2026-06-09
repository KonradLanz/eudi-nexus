#!/usr/bin/env python3
"""
pdf-ingest.py  —  ETSI PDF  →  corpus/specs/<stem>.json

Usage:
    npm run ingest                          # alle PDFs, idempotent
    npm run ingest -- --force               # alle PDFs, immer ueberschreiben
    npm run ingest:one -- path/to/file.pdf  # Einzeldatei

Output pro Norm (corpus/specs/<stem>.json):
    {
      "norm":        "EN 319 403",
      "shortname":   "TSP-Audit",
      "version":     "v2.2.2",
      "source_pdf":  "...",
      "ingested_at": "2026-06-10T00:00:00Z",
      "page_count":  42,
      "pages":       [ { "page_nr", "anchor", "section", "section_title", "text_clean" } ]
    }

Kurzname-Schema: konsistent mit corpus/LESEHILFE.md und corpus/READING-GUIDE.md
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("[ERROR] pdfplumber nicht gefunden. Bitte: npm run setup:py")


# ---------------------------------------------------------------------------
# Kurzname-Mapping  (kanonisch, konsistent mit LESEHILFE.md / READING-GUIDE.md)
# ---------------------------------------------------------------------------
# Schluessel: normalisierte Norm-ID ohne Leerzeichen, z.B. "EN319401"
# Wert: Kurzname gemaess Lesehilfe
SHORTNAMES: dict[str, str] = {
    # Fundament
    "RFC5280":    "PKIX",
    "RFC6960":    "OCSP",
    "RFC5652":    "CMS",
    # Signaturformate (1xx)
    "EN319122":   "CAdES",
    "EN3191221":  "CAdES",       # Teil 1
    "EN3191222":  "CAdES-2",     # Teil 2
    "EN319132":   "XAdES",
    "EN3191321":  "XAdES",
    "EN3191322":  "XAdES-2",
    "EN319142":   "PAdES",
    "EN3191421":  "PAdES",
    "EN3191422":  "PAdES-2",
    "EN319182":   "JAdES",
    # Signaturprozess (2xx)
    "EN319102":   "SigValid",
    "EN3191021":  "SigValid",
    "EN3191022":  "SigValid-2",
    "EN319122-1": "CAdES",
    # Zeitstempel (3xx)
    "EN319421":   "TSA-Policy",
    "EN319422":   "TSA-Profile",
    "EN319431":   "TSA-Conformance",
    # TSP (4xx)
    "EN319401":   "TSP-Baseline",
    "EN319403":   "TSP-Audit",
    "EN3194031":  "TSP-Audit",
    "EN3194032":  "TSP-Audit-2",
    "EN319411":   "QSign-CertPolicy",
    "EN3194111":  "QSign-CertPolicy",
    "EN3194112":  "QSeal-CertPolicy",
    "EN319412":   "QCert-Profile",
    "EN3194121":  "QCert-Profile-1",
    "EN3194122":  "QCert-Profile-2",
    "EN3194123":  "QCert-Profile-3",
    "EN3194124":  "QCert-Profile-4",
    "EN3194125":  "QCert-Profile-5",
    "EN319461":   "RemoteSign-Policy",
    # Preservation / LTA (5xx)
    "EN319511":   "LTA-Policy",
    "EN319512":   "LTA-Profile",
    # Trust Lists (6xx)
    "EN319601":   "TrustList-Format",
    "EN319611":   "EU-TrustList",
    "EN319612":   "TrustList-Profile",
    "EN3196121":  "TrustList-Profile-1",
    "EN3196122":  "TrustList-Profile-2",
    # Wallet-Schicht (46x / 47x / 49x) — als TS
    "TS119461":   "RemoteID",
    "TS119471":   "WalletTrust",
    "TS1194711":  "WalletTrust-1",
    "TS1194712":  "WalletTrust-2",
    "TS119491":   "QEAA-Profile",
    # Sonstige TS / TR
    "TS119101":   "ASiC",
    "TS1191011":  "ASiC-1",
    "TS1191012":  "ASiC-2",
    "TS119403":   "TSP-Audit-TS",
    "TS119612":   "TrustList-TS",
    "TS119615":   "TrustList-EU-TS",
}


def get_shortname(norm: str) -> str:
    """Schlaegt Kurzname nach, z.B. 'EN 319 401' -> 'TSP-Baseline'.
    Fallback: leerer String.
    """
    key = norm.replace(" ", "").replace("-", "")
    return SHORTNAMES.get(key, "")


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(
    r"^(?P<num>[A-Z]?\.?(?:\d+\.)+\d*|\.?\d+)\s+(?P<title>[A-Z][^\n]{2,80})$",
    re.MULTILINE,
)

# ETSI-Portal-Schema: en_319403v020202p.pdf
FILENAME_ETSI_RE = re.compile(
    r"^(?P<type>[a-z]+)_(?P<family>\d{3})(?P<number>\d{3,4})v"
    r"(?P<vmaj>\d{2})(?P<vmin>\d{2})(?P<vpatch>\d{2})p\.pdf$",
    re.IGNORECASE,
)

# Manuelles Schema: EN_319_401_v2.2.1.pdf
FILENAME_MANUAL_RE = re.compile(
    r"^(?P<norm>[A-Z]+_\d+_\d+(?:_\d+)?)_(?P<version>v[\d.]+)\.pdf$",
    re.IGNORECASE,
)

SKIP_PREFIXES = (".",)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def parse_filename(pdf_path: Path) -> tuple[str, str]:
    """Gibt (norm, version) zurueck.
    Beispiel: en_319403v020202p.pdf -> ('EN 319 403', 'v2.2.2')
    """
    name = pdf_path.name
    m = FILENAME_ETSI_RE.match(name)
    if m:
        doc_type = m.group("type").upper()
        family   = m.group("family")
        number   = m.group("number")
        norm     = f"{doc_type} {family} {number}"
        version  = f"v{int(m.group('vmaj'))}.{int(m.group('vmin'))}.{int(m.group('vpatch'))}"
        return norm, version
    m = FILENAME_MANUAL_RE.match(name)
    if m:
        return m.group("norm").replace("_", " "), m.group("version").lower()
    return pdf_path.stem, "vX.X.X"


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def extract_sections_from_page(text: str) -> list[tuple[str, str]]:
    return [(m.group("num").rstrip("."), m.group("title").strip())
            for m in SECTION_RE.finditer(text)]


def output_path(pdf_path: Path, corpus_root: Path) -> Path:
    specs_dir = corpus_root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    return specs_dir / (pdf_path.stem + ".json")


def collect_pdfs(inputs: list[str]) -> list[Path]:
    result: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
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
    norm, version   = parse_filename(pdf_path)
    shortname       = get_shortname(norm)
    shortname_label = f"  [{shortname}]" if shortname else ""

    if verbose:
        print(f"[INGEST] {pdf_path.name}  →  {norm} {version}{shortname_label}")

    pages_out: list[dict] = []
    current_section = ""
    current_title   = ""

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            page_nr = page.page_number
            raw     = page.extract_text(layout=False) or ""
            text    = clean_text(raw)
            hits    = extract_sections_from_page(text)
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
        "shortname":   shortname,
        "version":     version,
        "source_pdf":  str(pdf_path),
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page_count":  page_count,
        "pages":       pages_out,
    }

    out = output_path(pdf_path, corpus_root)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        chars = sum(len(p["text_clean"]) for p in pages_out)
        print(f"[OK]     {out}  ({page_count} Seiten, {chars:,} Zeichen)")
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETSI PDF → corpus/specs/*.json  (idempotent by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  npm run ingest                         # alle PDFs, bereits vorhandene ueberspringen
  npm run ingest -- --force              # alle PDFs, immer ueberschreiben
  npm run ingest -- downloads/specs/EN/  # nur EN-Unterordner
  npm run ingest:one -- path/to/file.pdf # Einzeldatei
""",
    )
    parser.add_argument(
        "input", nargs="+",
        help="PDF-Datei(en) oder Ordner (rekursiv). Standard: downloads/specs/",
    )
    parser.add_argument(
        "--corpus", default="corpus",
        help="Corpus-Wurzel (Standard: corpus/)",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Bereits vorhandene JSON-Dateien ueberschreiben",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Weniger Ausgabe",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus)
    verbose     = not args.quiet
    pdf_files   = collect_pdfs(args.input)

    if not pdf_files:
        sys.exit("[ERROR] Keine PDF-Dateien gefunden.")

    if verbose:
        print(f"[INFO]  {len(pdf_files)} PDFs gefunden  "
              f"({'--force: immer ueberschreiben' if args.force else 'idempotent: vorhandene ueberspringen'})")

    ok = skipped = errors = 0
    for pdf in pdf_files:
        out = output_path(pdf, corpus_root)
        if not args.force and out.exists():
            if verbose:
                print(f"[SKIP]  {pdf.name}")
            skipped += 1
            continue
        try:
            ingest_pdf(pdf, corpus_root, verbose=verbose)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {pdf.name}: {exc}", file=sys.stderr)
            errors += 1

    if verbose:
        print(f"\n[DONE]  {ok} verarbeitet | {skipped} uebersprungen | "
              f"{errors} Fehler  (von {len(pdf_files)} gesamt)")


if __name__ == "__main__":
    main()
