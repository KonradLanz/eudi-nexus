# migrate-norm-aliases — Schema-Migration für `norm_aliases`

`scripts/migrate-norm-aliases.py` ergänzt die bestehende `norm_aliases`-Tabelle
um vier neue Spalten und drei neue Indizes. Das Skript ist **idempotent** — es kann
beliebig oft ausgeführt werden, bereits vorhandene Spalten und Indizes werden
stillschweigend übersprungen.

---

## Hintergrund

Der ursprüngliche `norm_aliases`-Eintrag speicherte nur kanonischen Bezeichner (`norm`),
Alias-String (`alias`) und Typ (`alias_type`). Damit fehlten:

- ein **menschenlesbarer Label** für UI-Ausgaben (z. B. `EN 319 401 v3.3.1 – General Policy Requirements`)
- ein **versionsstabiler Kurzname** (`shortname`) für Fuzzy-Matching und Tool-Kontext
- eine **kanonische Kurzklammer** (`abbr`) wie `TSP-Baseline` für Token-arme LLM-Kontexte
- die **ETSI Work Item Number** (`wki_id`) für Weblinks und WKI-Upsert-Pipelines

---

## Neue Spalten

| Spalte         | Typ    | Bedeutung |
|----------------|--------|-----------|
| `display_name` | `TEXT` | Menschenlesbarer Label inkl. Version + Titel-Auszug, z. B. `EN 319 401 v3.3.1 – General Policy Requirements for TSPs` |
| `shortname`    | `TEXT` | KI-generierter Langname (versionsstabil, gem. `schema.md §2`), z. B. `ETSI EN 319 401 Trust Service Policy` |
| `abbr`         | `TEXT` | Kanonische Kurzklammer, z. B. `TSP-Baseline`; `NULL` wenn keine etablierte Abkürzung existiert |
| `wki_id`       | `TEXT` | ETSI Work Item Number, z. B. `DTR/ESI-0019401`; `NULL` wenn unbekannt |

---

## Neue Indizes

| Index                        | Spalten              | Bedingung               | Zweck |
|------------------------------|----------------------|-------------------------|-------|
| `norm_aliases_abbr_idx`      | `abbr`               | `WHERE abbr IS NOT NULL` | Schneller Abbr-Lookup (Fuzzy-First-Pass) |
| `norm_aliases_wki_idx`       | `wki_id`             | `WHERE wki_id IS NOT NULL` | WKI-Upsert und Weblink-Generierung |
| `norm_aliases_type_norm_idx` | `(alias_type, norm)` | –                       | Effizientes Filtern nach Typ + Norm |

---

## Verwendung

```bash
# Standard — DB-Pfad aus $MCP_DB_PATH oder Fallback corpus/eudi-nexus.db
python3 scripts/migrate-norm-aliases.py

# Expliziter Pfad
python3 scripts/migrate-norm-aliases.py --db path/to/custom.db

# Vorschau ohne Schreibzugriff
python3 scripts/migrate-norm-aliases.py --dry-run
```

Das Skript prüft via `PRAGMA table_info` welche Spalten bereits existieren und
führt nur fehlende `ALTER TABLE … ADD COLUMN`-Statements aus.

---

## Voraussetzungen

Die `norm_aliases`-Tabelle muss bereits existieren. Falls die DB noch leer ist:

```bash
python3 scripts/build-index.py   # legt Schema an + ingestiert Daten
```

> **Hinweis:** `build-index.py` benötigt `httpx` (`pip install httpx`).

---

## Nach der Migration

Die neuen Spalten sind initial `NULL`. Befüllung erfolgt durch:

1. **Re-Ingest:** `python3 scripts/build-index.py` (füllt alle Spalten neu auf)
2. **Standalone Back-fill:** `python3 scripts/backfill-norm-aliases.py` (geplant, siehe `TODO-MCP.md`)

---

## Idempotenz-Garantie

Beim erneuten Ausführen:

```
✔ Nothing to do — schema is already up to date.
```

Es werden **keine** Fehler geworfen, keine Daten verändert.

---

## Migrations-Version

Nach erfolgreicher Ausführung wird in `index_meta` gesetzt:

```sql
INSERT OR REPLACE INTO index_meta(key, value)
VALUES ('schema_migration', 'norm-aliases-v2');
```

Diese Version kann von `build-index.py` und anderen Skripten abgefragt werden,
um sicherzustellen, dass das Schema aktuell ist.
