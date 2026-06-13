# eudi-nexus — Arbeitsstand & offene Punkte

> Zuletzt aktualisiert: 2026-06-13

---

## ✅ Abgearbeitet

### Ingestion & Segmentierung

- [x] **PDF-Segmentierung** (`pdf-segment.py`) — Basisimplementierung mit `pdfplumber`, Block-Extraktion, TOC-Erkennung, RFC-2119-Klassifikation (NORM / INFORM / SECTION / HEADER / FOOTER)
- [x] **DOCX-Ingestion** (`docx-ingest.py`) — Unterstützung für Word-Dokumente
- [x] **TOC-Extraktion** (`toc-extract.py`, `toc-segment.py`) — Inhaltsverzeichnis-basiertes Segmentieren
- [x] **Merge-Logik** (`build-index.py`) — `_merge_short_segments()` mit `MERGE_MIN_CHARS`, `_can_merge()`, `_sentence_end()`; kurze Fragmente werden zu semantisch zusammenhängenden Absätzen zusammengeführt
- [x] **Rebuild-Bug analysiert** — `INSERT OR IGNORE` überschreibt keine bestehenden Segmente bei unvollständigem Drop; Workaround: DB-Datei vor Rebuild explizit löschen (`rm corpus/eudi-nexus.db`)
- [x] **`para-tune.py`** *(commit `b854fb6`)* — Adaptiver Absatzgrenzen-Kalibrator:
  - Grid-Search über `GAP_THRESHOLD` (8–40 pt) pro PDF
  - Qualitätsmetriken: Median-Länge ≥ 150 chars, Short-Frac ≤ 25 %
  - Ollama-Fallback (default `llama3`) klassifiziert Grenz-Kandidaten wenn Grid allein nicht ausreicht
  - Schreibt gelernte Regeln nach `corpus/para-tune/learned-rules.json`
  - `get_rule_for_stem()` als öffentliche API für `pdf-segment.py`
  - `--apply` re-segmentiert Corpus mit gelernten Regeln

### Index & Search

- [x] **FTS5-Index** (`build-index.py`) — SQLite Full-Text-Search mit `normative_keywords`, `section_path`
- [x] **Embedding-Pipeline** — Vektor-Embeddings für semantische Suche
- [x] **MCP-Server** (`mcp-server.py`) — Integration mit Claude Desktop / LM Studio
- [x] **Referenz-Extraktion** (`extract-references.js`, `crawl-references.js`) — Cross-Referenzen zwischen Specs
- [x] **Titel-Anreicherung** (`enrich-titles.js`) — Automatisches Befüllen leerer Abschnittstitel via lokalem LLM

### Tooling

- [x] **Health-Check** (`health-check.py`) — Prüft Vollständigkeit des Index
- [x] **Ingest-QA** (`ingest-qa.py`) — Qualitätssicherung nach Ingestion
- [x] **LM Studio MCP** (`install-lmstudio-mcp.sh`) — Lokaler AI-Stack Setup
- [x] **Local AI Helper** (`local-ai-helper.js`) — Wrapper für Ollama/LM Studio API

---

## 🔄 In Arbeit

- [ ] **`pdf-segment.py` ← `para-tune.py` integrieren**
  `get_rule_for_stem()` am Anfang von `pdf-segment.py` einlesen, damit gelernte `gap_threshold`- und `merge_endings`-Werte automatisch angewendet werden.
  *Aktuell muss `--apply` noch manuell ausgeführt werden.*

  ```python
  # Geplante Einbindung in pdf-segment.py (Zeile ~40):
  from scripts.para_tune import get_rule_for_stem
  _rule = get_rule_for_stem(Path(pdf_path).stem)
  _GAP_THRESHOLD = _rule.get("gap_threshold", 20)
  ```

- [ ] **`para-tune.py` erstmalig gegen echtes Corpus ausführen**
  `python3 scripts/para-tune.py` auf allen PDFs durchlaufen,
  `corpus/para-tune/learned-rules.json` befüllen,
  Qualitätsmetriken dokumentieren.

- [ ] **DB-Rebuild zuverlässig machen**
  `drop_all()` in `build-index.py` prüfen — WAL-Artefakte können dazu führen, dass
  `INSERT OR IGNORE` alte Einträge nicht überschreibt.
  Empfehlung: `os.remove(db_path)` vor `init_db()` einfügen wenn `--rebuild` gesetzt.

---

## 📋 Offen / Backlog

### Segmentierungsqualität

- [ ] **Tabellenzellen aus NORM-Segmenten filtern** — Tabellenzellen mit RFC-2119-Keywords werden fälschlicherweise als NORM klassifiziert
- [ ] **Anhang-Erkennung verbessern** — "Annex A (normative)" vs. "Annex B (informative)" zuverlässig unterscheiden; aktuell werden beide als NORM klassifiziert
- [ ] **Fußnoten-Erkennung** — Kleine Schriftgröße + untere Seitenposition als Fußnoten-Signal nutzen, damit Fußnotentext nicht in NORM-Segmente wandert
- [ ] **Mehrsprachige Specs** — EN-Specs mit französischen / deutschen Annex-Texten; Klassifikation für nicht-englische Absätze verbessern
- [ ] **`para-tune` Visualisierung** — Gap-Threshold-Metriken als HTML-Report ausgeben (`--report`-Flag)

### Index & Retrieval

- [ ] **Hybrid-Suche** — FTS5 + Vektor-Ähnlichkeit per RRF-Fusion kombinieren
- [ ] **Sektions-Hierarchie exposieren** — `section_path` als navigierbaren Baum im MCP-Server
- [ ] **Delta-Updates** — Nur geänderte/neue Specs re-indexieren statt Voll-Rebuild
- [ ] **Normative-Keyword-Gewichtung** — SHALL > SHOULD > MAY im Relevanz-Ranking

### Corpus

- [ ] **Automatischer Download neuer Spec-Versionen** — `download-oidf-specs.js` auf ETSI-Portalstruktur erweitern
- [ ] **Versionserkennung** — Neue Spec-Version erscheint → alte Version als deprecated markieren
- [ ] **IETF RFC Ingestion** — RFCs als zusätzliche Quellen einbinden (plain-text Format, kein PDF nötig)

### CI & Automatisierung

- [ ] **CI-Pipeline** — GitHub Actions: bei neuem PDF-Commit automatisch `para-tune.py --stats` ausführen und Ergebnis als Step-Summary ausgeben
- [ ] **Web-UI für Segment-Review** — Manuelle Korrektur falsch klassifizierter Segmente mit Feedback-Loop zurück in `para-tune`

---

## 🏗 Architektur-Überblick

```
PDF / DOCX
    │
    ▼
pdf-segment.py / docx-ingest.py
    │  ← liest corpus/para-tune/learned-rules.json
    │  (gap_threshold, merge_endings per Stem)
    ▼
corpus/specs/_segments/*.segments.json
    │
    ▼
build-index.py  →  corpus/eudi-nexus.db (SQLite + FTS5 + Embeddings)
    │
    ▼
mcp-server.py  →  Claude Desktop / LM Studio
```

**Kalibrierungs-Loop:**

```bash
# 1. Aktuelle Qualität messen
python3 scripts/para-tune.py --stats

# 2. Kalibrieren (Grid + Ollama)
python3 scripts/para-tune.py

# 3. Corpus neu segmentieren
python3 scripts/para-tune.py --apply

# 4. Index neu bauen
rm corpus/eudi-nexus.db
python3 scripts/build-index.py
```
