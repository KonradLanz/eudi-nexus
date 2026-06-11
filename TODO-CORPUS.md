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

---

## npm-Scripts (geplant)

```json
"segment":    ".venv/bin/python3 scripts/page-to-segment.py",
"segment:one":"... --pdf downloads/specs/TS/ts_119512v010201p.pdf",
"asciidoc":   ".venv/bin/python3 scripts/segment-to-adoc.py",
"xref":       ".venv/bin/python3 scripts/resolve-xrefs.py"
```

---

## Bezug zu TODO-MCP.md

Die Corpus-Pipeline ist Voraussetzung für die MCP-Server-Verbesserungen in
`TODO-MCP.md`. Insbesondere:

- Bessere Sektionsgrenzen → `get_section()` liefert vollständige Abschnitte
- Cross-Ref-Auflösung → `search_norm()` kann verwandte Segmente vorschlagen
- AsciiDoc-Export → Claude Desktop kann ganze Normen als Dokument lesen
