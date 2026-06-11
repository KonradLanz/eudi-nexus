# TODO-CORPUS — Corpus Pipeline Roadmap

> Stand: 2026-06-11  
> Kontext: 154 ETSI-PDFs, alle TOC-JSONs erfolgreich extrahiert (0 errors).

---

## Status quo

| Schritt | Script | Status |
|---|---|---|
| PDF-Download | `scripts/download.py` | ✅ 154 PDFs |
| TOC-Extraktion | `scripts/toc-extract.py` | ✅ 154/154, 0 errors |
| Segment-Extraktion | `scripts/ingest.py` | ⚠️ läuft, nutzt TOC noch nicht |
| AsciiDoc-Generierung | — | ❌ noch nicht vorhanden |
| Cross-Reference-Auflösung | — | ❌ noch nicht vorhanden |
| Versions-Management | — | ❌ noch nicht vorhanden |
| MCP-Server Re-Ingest | `npm run ingest` | 🔄 nach Pipeline-Fixes wiederholen |

---

## Phase 1 — Segment-Extraktion auf TOC-Basis

### Problem heute
`ingest.py` schneidet Segmente nach festen Seitenzahlen oder Zeilenanzahl — ohne zu
wissen, wo Abschnittsgrenzen liegen. Dadurch landen normative Anforderungen aus
Abschnitt 7.3.1 und 7.3.2 im gleichen Segment.

### Ziel
Jedes Segment = genau ein TOC-Eintrag (Leaf-Node), d.h. die kleinste benannte
Einheit im Dokument. Seitengrenzen kommen aus dem TOC-JSON.

### Implementierung: `scripts/page-to-segment.py`

```
corpus/toc/ts_119512.toc.json
    → liest toc[].page Ranges
    → extrahiert Seitentext via pdfplumber
    → schreibt corpus/segments/ts_119512/
          7.3.adoc        (Eltern-Sektion, nur Heading + direkt darunter)
          7.3.1.adoc      (pages 45–47)
          7.3.2.adoc      (pages 47–49)
          ...
```

**Seitenbereich berechnen:**
```python
# toc ist nach page sortiert
for i, entry in enumerate(toc):
    start_page = entry["page"]
    end_page   = toc[i+1]["page"] - 1 if i+1 < len(toc) else total_pages
```

**Output pro Segment (JSON):**
```json
{
  "id":        "ts_119512#7.3.1",
  "norm":      "ts_119512",
  "version":   "01.02.01",
  "num":       "7.3.1",
  "title":     "Subject registration",
  "page_from": 45,
  "page_to":   47,
  "text":      "...",
  "parent":    "ts_119512#7.3",
  "depth":     3,
  "type":      "norm"   // norm | inform | annex | boilerplate
}
```

---

## Phase 2 — AsciiDoc-Generierung

### Ziel
Pro Dokument ein lesbares `.adoc` mit korrekter Hierarchie und funktionierenden
internen Querverweisen.

### Struktur
```asciidoc
// ts_119512v010201p.adoc — auto-generated, do not edit

= TS 119 512: Electronic Signatures and Infrastructures (ESI)
:doctype: book
:toc:

[[ts_119512-7]]
== 7 Requirements on TSP practice

[[ts_119512-7.3]]
=== 7.3 Public key infrastructure

[[ts_119512-7.3.1]]
==== 7.3.1 Subject registration

...text...

See <<ts_119512-7.3.2>>.
```

### Cross-Reference-Regex
Beim Text-Export alle `clause X.Y.Z` / `section X.Y` Verweise durch AsciiDoc-Xrefs
ersetzen:

```python
_CLAUSE_REF_RE = re.compile(
    r"\b(clause|section|annex)\s+(?P<ref>[A-Z]?\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
# Ersetzung: "clause 7.3.1" → "<<ts_119512-7.3.1,clause 7.3.1>>"
```

Externe Verweise (auf andere Normen, z.B. "see clause 6 of [2]") werden separat
behandelt — `[2]` aus dem References-Abschnitt auflösen und als externen Link
annotieren.

---

## Phase 3 — Interne Verweise ("above", "below")

### Typen und Behandlung

| Verweis-Typ | Vorkommen | Strategie |
|---|---|---|
| `clause X.Y.Z` | sehr häufig | → AsciiDoc-Xref (Phase 2) |
| `see above` / `as noted above` | häufig | Kontext-Fenster: prev_section im Segment mitführen |
| `the following` / `as follows` | häufig | meist einleitend → kein Auflösungsbedarf |
| `Table 3` / `Figure 5-1` | mittel | → `[[ts_119512-table-3]]` Anker |
| `in accordance with [2]` | häufig | Normreferenz aus References-Sektion auflösen |

### Pragmatischer Ansatz für v1 (MCP-Server)
- Jedes Segment bekommt `prev_section_id` und `next_section_id`
- Der MCP-Server liefert bei `get_segment()` optional auch die Nachbar-Segmente mit
- Für `clause X.Y.Z` Verweise: Feld `cross_refs: ["ts_119512#7.3.2", ...]` im Segment-JSON
- Relative Verweise ("above") → für v1 ignorieren, da normative Aussagen eigenständig sind

### Erweiterung für v2
BM25-Suche innerhalb des gleichen Dokuments rückwärts vom aktuellen Segment für
"see above"-Auflösung. Kandidaten als `intra_doc_refs` annotieren.

---

## Phase 4 — Re-Ingest in SQLite / MCP-Server

Nach Phase 1–2:

```bash
npm run ingest --force   # liest corpus/segments/**/*.json
npm run mcp:restart      # MCP-Server neu starten
```

Der MCP-Server bekommt dann:
- Korrekte Sektionsgrenzen (keine halben Abschnitte mehr)
- `get_section("ts_119512", "7.3")` liefert alle Sub-Segmente aus 7.3.x
- `search_norm("subject registration")` findet Segment `ts_119512#7.3.1` statt
  einen zufälligen Ausschnitt

---

## Phase 5 — Versions-Management

### Annahme (gültig für den gesamten Corpus)

> **Alle Normreferenzen werden als Verweise auf die jeweils neueste Version
> behandelt.** Der MCP-Server antwortet ausschließlich aus der neuesten Version
> jeder Norm. Ältere Versionen bleiben im Corpus, werden aber beim Ingest nicht
> in die SQLite-DB geladen.

Diese Annahme entspricht dem ETSI-Standard für undatierte Referenzen und vereinfacht
die Implementierung erheblich.

---

### 5a — Version aus Dateiname parsen

ETSI-Dateinamen enthalten die Version explizit:
```
ts_119512v010201p.pdf   →  v01.02.01
ts_119512v010301p.pdf   →  v01.03.01  ← neuere Version → wird verwendet
```

```python
def parse_version(filename: str) -> tuple[int, int, int] | None:
    m = re.search(r'v(\d{2})(\d{2})(\d{2})p', filename)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    return None

def find_latest(filenames: list[str]) -> str:
    """Gibt den Dateinamen mit der höchsten Versionsnummer zurück."""
    return max(filenames, key=lambda f: parse_version(f) or (0, 0, 0))
```

---

### 5b — Norm-ID → Latest-Version-Mapping beim Ingest

`ingest.py` gruppiert vor dem Einlesen alle PDFs nach `norm_id` und wählt pro
Gruppe nur die neueste Version:

```python
from collections import defaultdict

pdf_groups: dict[str, list[str]] = defaultdict(list)
for pdf in all_pdfs:
    norm_id = re.sub(r'v\d+p\.pdf$', '', pdf.name)  # "ts_119512"
    pdf_groups[norm_id].append(pdf.name)

# Nur jeweils neueste Version verarbeiten
to_ingest = [find_latest(v) for v in pdf_groups.values()]
```

SQLite bekommt eine Hilfstabelle:
```sql
CREATE TABLE norm_latest (
  norm_id   TEXT PRIMARY KEY,  -- "ts_119512"
  version   TEXT NOT NULL,     -- "01.03.01"
  filename  TEXT NOT NULL      -- "ts_119512v010301p.pdf"
);
```

---

### 5c — MCP-Server: nur latest-Segmente ausliefern

Alle Queries gegen `segments` joinen auf `norm_latest`:

```sql
-- Segment-Suche: automatisch nur neueste Versionen
SELECT s.*
FROM   segments s
JOIN   norm_latest nl ON s.norm = nl.norm_id AND s.version = nl.version
WHERE  ...
```

Dadurch ist es strukturell unmöglich, aus einer alten Version zu antworten —
auch wenn mehrere Versionen im Corpus liegen.

---

### 5d — Warnung bei zitierten Normreferenzen

Beim Parsen des References-Abschnitts (Phase 3) wird geprüft, ob die zitierte
Norm im Corpus in einer neueren Version vorliegt als die Norm selbst:

```python
def check_ref_currency(citing_norm_id: str, ref: NormRef) -> Warning | None:
    latest_citing = db.get_latest_version(citing_norm_id)
    latest_cited  = db.get_latest_version(ref.norm_id)

    if latest_cited is None:
        return Warning(
            level="info",
            msg=f"{ref.norm_id} referenced but not in corpus"
        )

    # Ist die zitierende Norm selbst schon alt?
    # (Kann passieren wenn wir eine ältere Norm importiert haben)
    # → kein separater Check nötig, da ingest.py nur latest lädt.

    return None  # alles ok — wir haben die neueste Version beider Normen
```

**Wann wird gewarnt?**

| Situation | Warnung |
|---|---|
| Referenzierte Norm nicht im Corpus | `⚠️ ts_119411-1 not in corpus` |
| Referenzierte Norm im Corpus, aber nur ältere Version | *(kann nach Phase 5b nicht mehr auftreten)* |
| Citing-Norm ist selbst veraltet | *(kann nach Phase 5b nicht mehr auftreten)* |
| Alles ok | kein Output |

In der Praxis reduziert sich Phase 5d damit auf: **fehlende Normen im Corpus
erkennen** — d.h. es gibt eine Referenz auf eine Norm die wir noch nicht
heruntergeladen haben.

---

### 5e — `npm run check-refs` Script

```bash
# Zeigt alle Referenzen die auf Normen zeigen die nicht im Corpus sind
npm run check-refs

> Missing from corpus:
>   ETSI TS 119 611  (referenced by ts_119512, ts_119401)
>   ETSI EN 319 132  (referenced by ts_119512)
```

Das Script liest `corpus/segments/**/*.json`, sammelt alle `cross_refs` auf
andere Normen und prüft gegen `norm_latest`. Fehlende Normen → Download-Liste
für `scripts/download.py`.

---

## Offene Fragen / Design-Entscheidungen

- [ ] **Granularität**: Leaf-Nodes oder auch Parent-Nodes als eigene Segmente?
      Empfehlung: beide, Parent-Segment = aggregierter Text aller Kinder (für
      Übersichts-Queries).

- [ ] **Überlappende Seiten**: Manche Sektionen beginnen mitten auf einer Seite.
      pdfplumber `crop()` kann Koordinaten-basiert schneiden — aufwändig aber
      präzise. v1: Seiten-granular, v2: Koordinaten-granular.

- [ ] **Tabellen**: pdfplumber kann Tabellen als strukturierte Daten extrahieren
      (`extract_tables()`). Für normative Tabellen (z.B. Algorithmus-Anforderungen)
      wäre das wertvoller als Fließtext. Separates TODO.

- [ ] **Passwortgeschützte PDFs**: `--timeout 30` fängt Hänger ab, aber manche
      PDFs sind encrypted und liefern leere Texte. Detektion: `total_pages > 0`
      aber alle `_page_text()` Ergebnisse leer → als `encrypted: true` markieren.

- [ ] **AsciiDoc vs. Markdown**: AsciiDoc hat native Cross-Reference-Syntax und
      wird von Antora/Asciidoctor unterstützt. Markdown bräuchte manuelle Anker.
      Empfehlung: AsciiDoc für das ganze Projekt beibehalten.

- [ ] **Corpus-Vollständigkeit**: `npm run check-refs` (Phase 5e) wird zeigen
      welche referenzierten Normen noch fehlen. Diese nachträglich downloaden
      und in den Ingest aufnehmen.

---

## npm-Scripts (geplant)

```json
"segment":     ".venv/bin/python3 scripts/page-to-segment.py",
"segment:one": "... --pdf downloads/specs/TS/ts_119512v010201p.pdf",
"asciidoc":    ".venv/bin/python3 scripts/segment-to-adoc.py",
"xref":        ".venv/bin/python3 scripts/resolve-xrefs.py",
"check-refs":  ".venv/bin/python3 scripts/check-refs.py"
```

---

## Bezug zu TODO-MCP.md

Die Corpus-Pipeline ist Voraussetzung für die MCP-Server-Verbesserungen in
`TODO-MCP.md`. Insbesondere:

- Bessere Sektionsgrenzen → `get_section()` liefert vollständige Abschnitte
- Cross-Ref-Auflösung → `search_norm()` kann verwandte Segmente vorschlagen
- AsciiDoc-Export → Claude Desktop kann ganze Normen als Dokument lesen
- Latest-only Policy → MCP-Server antwortet strukturell nie aus veralteten Versionen
