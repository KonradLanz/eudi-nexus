# TODO: MCP Server Implementation

Schrittweise Implementierung des lokalen MCP-Servers.
Architektur-Übersicht: [`docs/architecture/mcp-server.md`](docs/architecture/mcp-server.md)
Embedding-Modell-Entscheidung: [`docs/architecture/embedding-models.md`](docs/architecture/embedding-models.md)

---

## ✅ TODO 1 — `scripts/build-index.py` — ERLEDIGT

- [x] Alle `_segments/*.segments.json` einlesen
- [x] SQLite-DB `corpus/eudi-nexus.db` anlegen (Schema: segments + vec-Tabelle)
- [x] FTS5-Index über `text`-Spalte anlegen (BM25 out-of-the-box)
- [x] Embeddings via `LMSTUDIO_BASE_URL/v1/embeddings` oder Ollama abrufen
- [x] Embeddings in `sqlite-vec` virtual table speichern
- [x] Idempotent: bereits indizierte Segmente (by `id`) überspringen
- [x] `--rebuild` Flag: DB komplett neu aufbauen
- [x] Progress-Output

---

## ✅ TODO 2 — `scripts/mcp-server.py` — ERLEDIGT

- [x] `search_norm(query, norm?, types?, limit?, alpha?)` — hybrid BM25+cosine
- [x] `get_segment(segment_id)` — Segment by ID
- [x] `get_section(norm, section, types?)` — alle Segmente einer Section
- [x] `list_norms()` — alle indizierten Normen
- [x] LM Studio + Ollama auto-detect (Fallback auf BM25-only)
- [x] 33 Tests, vollständig offline lauffähig (`pytest test/test_mcp_server.py`)

---

## ✅ TODO 3 — `.env.example` erweitern — ERLEDIGT

- [x] `EMBEDDING_MODEL=nomic-embed-text-v1.5`
- [x] `EMBEDDING_DIMENSIONS=768`
- [x] `MCP_DB_PATH=corpus/eudi-nexus.db`

---

## ✅ TODO 4 — `requirements.txt` erweitern — ERLEDIGT

- [x] `fastmcp`
- [x] `sqlite-vec`
- [x] `httpx`

---

## ✅ TODO 5 — `corpus/eudi-nexus.db` zu `.gitignore` — ERLEDIGT

- [x] `corpus/*.db` in `.gitignore`

---

## ✅ TODO 6 — `scripts/install-lmstudio-mcp.sh` — ERLEDIGT

Installscript für LM Studio MCP-Integration:

```bash
bash scripts/install-lmstudio-mcp.sh           # registrieren
bash scripts/install-lmstudio-mcp.sh --dry-run  # preview
bash scripts/install-lmstudio-mcp.sh --remove   # rückgängig
```

Was das Script tut:
- [x] Pre-flight checks: Python, fastmcp, sqlite_vec, DB vorhanden?
- [x] Schreibt `~/Library/Application Support/LM-Studio/mcp-servers/eudi-nexus.json`
- [x] 2-Sekunden Smoke-Test: Server startet fehlerfrei
- [x] Klare Ausgabe mit ✔ / ⚠ / ✘
- [x] `--dry-run` zeigt Config ohne zu schreiben
- [x] `--remove` löscht die Registrierung wieder

---

## Offene Punkte

### Nächster Schritt: Live-Test mit echten Daten

```bash
# 1. DB aufbauen (falls noch nicht vorhanden)
python scripts/build-index.py

# 2. MCP in LM Studio registrieren
bash scripts/install-lmstudio-mcp.sh

# 3. LM Studio neu starten
# 4. Chat öffnen → Tools-Icon → eudi-nexus Tools sollten erscheinen
```

### Zukünftige Erweiterungen (optional)

- [ ] `get_requirements(norm, section?)` — nur NORM-Segmente, sortiert nach Section
- [ ] `cite_clause(segment_id)` — Zitationsformat (Normname + Version + Anchor)
- [ ] Claude Desktop Konfiguration analog zu LM Studio
- [ ] GitHub Actions CI: `pytest -m "not embedding"` bei jedem Push
