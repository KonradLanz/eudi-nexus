# TODO: MCP Server Implementation

Schrittweise Implementierung des lokalen MCP-Servers.
Architektur-Übersicht: [`docs/architecture/mcp-server.md`](docs/architecture/mcp-server.md)
Embedding-Modell-Entscheidung: [`docs/architecture/embedding-models.md`](docs/architecture/embedding-models.md)

---

## TODO 1 — `scripts/build-index.py`

**Zweck:** Liest alle `corpus/specs/_segments/*.segments.json` und baut
einen SQLite-Index mit FTS5 (BM25) + sqlite-vec (Embeddings).

**Abhängigkeiten:**
```
pip install sqlite-vec httpx
```

**Was das Script tun soll:**
- [ ] Alle `_segments/*.segments.json` einlesen
- [ ] SQLite-DB `corpus/eudi-nexus.db` anlegen (Schema: segments + vec-Tabelle)
- [ ] FTS5-Index über `text`-Spalte anlegen (BM25 out-of-the-box)
- [ ] Für jeden Segment-Chunk: Embedding via `LMSTUDIO_BASE_URL/v1/embeddings` abrufen
- [ ] Embeddings in `sqlite-vec` virtual table speichern
- [ ] Idempotent: bereits indizierte Segmente (by `id`) überspringen
- [ ] `--rebuild` Flag: DB komplett neu aufbauen
- [ ] `--model` Flag: Embedding-Modell überschreiben (default aus `.env`)
- [ ] Progress-Output: Chunks/s, geschätzte Restzeit

**Chunk-Strategie:**
- NORM/INFORM-Segmente: direkt als Chunk (sind bereits ~300–600 Token)
- SECTION-Segmente: als Metadaten-Chunk (kein Embedding nötig, nur FTS)
- HEADER/FOOTER/TOC/OTHER: werden nicht indiziert

**npm-Script:**
```json
"index": "python3 scripts/build-index.py",
"index:rebuild": "python3 scripts/build-index.py --rebuild"
```

---

## TODO 2 — `scripts/mcp-server.py`

**Zweck:** FastMCP-Server der `corpus/eudi-nexus.db` als MCP-Tools exponiert.

**Abhängigkeiten:**
```
pip install fastmcp sqlite-vec httpx
```

**Tools zu implementieren:**

### `search_norm(query, norm?, section?, type?, top_k?)`
- [ ] Query embedden via LM Studio
- [ ] Hybrid-Search: `0.6 × BM25 + 0.4 × cosine`
- [ ] Filter: `norm`, `section`, `type` (NORM/INFORM/SECTION)
- [ ] Response: Liste von Segment-Objekten mit `anchor` (PDF-Backref)
- [ ] Default `top_k=5`

### `get_requirements(norm, section?)`
- [ ] Alle Segmente mit `type=NORM` für gegebene Norm
- [ ] Optional: gefiltert nach `section` prefix
- [ ] Sortiert nach `section` → `page`
- [ ] Response: Segmente mit `normative_keywords` hervorgehoben

### `cite_clause(segment_id)`
- [ ] Einzelnen Segment-Eintrag by `id` laden
- [ ] Response: Volltext + alle Metadaten + `anchor` für PDF-Link
- [ ] Normname + Version als Zitationsformat mitgeben

**Resource:**

### `norms://list`
- [ ] Alle indizierten Normen aus DB lesen
- [ ] Pro Norm: `norm`, `version`, `segment_count`, `norm_count` (NORM-type)

**npm-Script:**
```json
"mcp": "python3 scripts/mcp-server.py"
```

**Client-Konfiguration** → siehe `docs/architecture/mcp-server.md`

---

## TODO 3 — `.env.example` erweitern

- [ ] `EMBEDDING_MODEL=nomic-embed-text-v1.5` ergänzen
- [ ] `EMBEDDING_DIMENSIONS=768` ergänzen
- [ ] `MCP_DB_PATH=corpus/eudi-nexus.db` ergänzen

---

## TODO 4 — `requirements.txt` erweitern

- [ ] `fastmcp` ergänzen
- [ ] `sqlite-vec` ergänzen
- [ ] `httpx` ergänzen

---

## TODO 5 — `corpus/eudi-nexus.db` zu `.gitignore`

- [ ] `corpus/*.db` in `.gitignore` eintragen
  (DB wird lokal gebaut, nicht committed)

---

## Reihenfolge

1. `TODO 3` + `TODO 4` + `TODO 5` — Setup (5 min)
2. `TODO 1` — `build-index.py` implementieren und testen
3. `TODO 2` — `mcp-server.py` implementieren und in Claude Desktop einbinden
