# Parallelisierung TODO

## Sofort sinnvoll

- [ ] `scripts/ingest.py` parallelisieren: Der Hauptloop läuft aktuell sequenziell über alle Dokumente und ruft `run_ingest(...)` nacheinander auf.[cite:307]
- [ ] `scripts/pdf-segment.py` auf Dokumentebene parallelisieren: Die Segmentierung arbeitet stark dokumentorientiert; das ist ein guter Kandidat für einen Worker-Pool statt serieller Verarbeitung.[cite:307]
- [ ] `scripts/build-index.py` beim Embedding/API-Teil prüfen und parallelisieren: Es nutzt Batches (`EMBED_BATCH = 16`), aber wirkt insgesamt noch stark sequenziell über Segmente/Dokumente.[cite:308]
- [ ] `scripts/download-specs.js` auf kontrollierte Parallelität erweitern: Es gibt schon punktuell `Promise.all`, aber kein klares Download-Concurrency-Limit für viele Work Items.[cite:306]

## Pipeline-Ebene

- [ ] Unabhängige Schritte der Pipeline entkoppeln, wo Outputs nicht direkt voneinander abhängen. Die aktuelle npm-Pipeline ist komplett seriell: `ingest -> ingest:resources -> toc -> segment -> index`.[cite:306]
- [ ] Prüfen, ob `ingest:resources` parallel zu `toc` oder `segment` laufen kann, da Resource-Ingest nicht auf PDF-Verarbeitung angewiesen ist.[cite:306]
- [ ] Rebuild-Pipelines analog prüfen: auch `pipeline:full` und `pipeline:docx` sind vollständig seriell.[cite:306]

## Change-Detection (Speedup für geänderte Inputs)

### Ist-Zustand der Pipeline

```
download-specs.js   →  .headers.json Sidecar (ETag + Last-Modified + Content-Length) ✔ï¸
ingest.py           →  mtime-Vergleich output.json vs. source PDF/DOCX             ✔ï¸
pdf-segment.py      →  kein Skip-Check erkennbar                                  ❌
build-index.py      →  reiner Existenz-Key in index_meta (kein Hash, kein mtime)  ❌
```

Die Kette reißt ab Segmentierung. `.segments.json` hat zwar `segmented_at` und `segment_count` im Root, aber keinen Hash.

### Hash-Strategie

**Grundprinzip: jeder Schritt hasht seinen eigenen direkten Input.**  
Eine reine ETag-Propagation reicht nicht, weil man später in der Pipeline einsteigen kann
(z.B. nur `build-index.py` neu laufen lassen) und dann der ETag gar nicht verfügbar ist.
Jeder Schritt muss für sich selbst entscheiden können ob sein Input neu ist.

```
Schritt            Input-Datei              Hash gespeichert in
───────────────────────────────────────────────────────────────────────────────
download-specs.js  remote resource          .headers.json Sidecar (ETag)  ✔ï¸ bereits
ingest.py          PDF/DOCX                 mtime-Vergleich               ✔ï¸ bereits
pdf-segment.py     corpus/*.json            corpus/*.json „segmented_hash“-Feld  ← neu
build-index.py     .segments.json           index_meta „content_hash:stem“-Key  ← neu
```

- **CRC32 für alle neuen Hashes** (`zlib.crc32`, ~3–5× schneller als SHA-256, <1ms auf 40–234 KB):  
  ```python
  import zlib
  def crc32hex(path: Path) -> str:
      return f"{zlib.crc32(path.read_bytes()) & 0xFFFFFFFF:08x}"
  ```
- **ETag optional als Bonus** weiterreichen: wenn verfügbar ins Feld schreiben, aber nie als einzige Change-Detection-Quelle.
- **ingest.py mtime bleibt** — ist schnell genug und bricht nichts.

### Konkrete Schritte

- [ ] **`pdf-segment.py`:** ETag aus dem korrespondierenden `corpus/*.json` lesen und als `source_etag` ins `.segments.json`-Root schreiben. Beim nächsten Lauf: wenn `source_etag` unverändert → Skip.

- [ ] **`build-index.py` — `already_indexed()` ersetzen:**  
  ```python
  # statt: SELECT 1 FROM index_meta WHERE key = 'indexed:stem'
  # neu:
  import zlib, json
  def _file_crc32(path: Path) -> str:
      data = path.read_bytes()
      return hex(zlib.crc32(data) & 0xFFFFFFFF)

  def already_indexed(con, source_file, path) -> bool:
      row = con.execute(
          "SELECT value FROM index_meta WHERE key = ?",
          (f"content_hash:{source_file}",)
      ).fetchone()
      if row is None: return False
      return row["value"] == _file_crc32(path)  # oder source_etag aus JSON lesen
  ```
  Beim `mark_indexed()` entsprechend CRC32/ETag statt Timestamp speichern.

- [ ] **Schema-Erweiterung `index_meta`:**  
  `key=content_hash:stem → value=<crc32hex oder etag-string>`  
  Rückwärtskompatibel: alter `indexed:stem`-Key kann bleiben oder migriert werden.

- [ ] **Ausgabe beim Skip differenzieren:**  
  `⏭️  unchanged (crc32 match)` vs. `🔄  changed → reindex` vs. `➕  new`

- [ ] **Analoges Muster für `ingest.py`:**  
  Schon mtime-basiert — prüfen ob ETag-Propagation den mtime-Vergleich ergänzen sollte (z.B. wenn Datei touchä wurde ohne Änderung).

## Bereits erledigt

- [x] `scripts/toc-extract.py` parallelisiert: hat jetzt `--workers` und nutzt mehrere Prozesse statt eines rein sequenziellen Batch-Loops.[cite:306]

## Technische Leitplanken

- [ ] Einheitliches `--workers`/`--concurrency`-CLI-Schema für alle schweren Scripts einführen.
- [ ] Idempotenz beibehalten: zuerst Cache-/mtime-Skip sammeln, nur echte `todo`-Jobs parallel dispatchen.
- [ ] Ausgabe stabil halten: Fortschritt darf in Completion-Reihenfolge kommen, End-Summary aber deterministisch.
- [ ] CPU-lastige Schritte mit `ProcessPoolExecutor`, I/O-/Netzwerk-lastige Schritte mit begrenzter async/Promise-Concurrency umsetzen.
- [ ] Nicht gleichzeitig zu viele LM-Studio-Embedding-Requests feuern, sonst drohen Timeouts oder Throughput-Einbruch.[cite:308]

## Nächste Analyse

- [ ] `scripts/ingest.py` im Detail vermessen: CPU vs. I/O vs. Subprocess-Anteil.
- [ ] `scripts/pdf-segment.py` Hotspots messen: PDF-Parsing, Tabellenextraktion, DOCX-Stilklassifikation, JSON-Schreiben.[cite:307]
- [ ] `scripts/build-index.py` Hotspots messen: JSON-Read, Merge, Embedding-HTTP, SQLite-Writes.[cite:308]
- [ ] `scripts/download-specs.js` Request-Muster prüfen und ein sicheres Parallelitätslimit definieren.
