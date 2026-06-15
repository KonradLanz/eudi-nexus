-- migrate-norm-aliases.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: norm_aliases — Spalten-Erweiterung für Display-Metadaten
--
-- Zweck:
--   Ergänzt die bestehende norm_aliases-Tabelle um menschenlesbare Felder,
--   damit list_norms() und _resolve_norm() keine In-Memory-Assembly mehr
--   benötigen. Nach der Migration liefert jede Alias-Zeile direkt:
--
--     display_name   "EN 319 401 v3.3.1 — General Policy Requirements for TSP"
--     shortname      "TrustServicePolicy-Baseline"   (KI-generiert, schema.md §2)
--     abbr           "TSP-Baseline"                  (kanonische Klammer)
--     wki_id         "0019401"                        (ETSI Work Item key, optional)
--
-- Idempotenz:
--   Jedes ALTER TABLE verwendet IF NOT EXISTS-Simulation via
--   "ADD COLUMN … DEFAULT NULL" — SQLite ignoriert kein Duplikat automatisch,
--   daher prüft das Python-Wrapper-Script ob die Spalte bereits existiert.
--   Dieses .sql-File darf direkt mit `sqlite3 corpus/eudi-nexus.db < ...`
--   ausgeführt werden; es schlägt fehl falls eine Spalte bereits existiert
--   (harmlos — sqlite3 gibt einen Fehler, aber die DB bleibt konsistent).
--   Für idempotenten Betrieb: migrate-norm-aliases.py verwenden (s. unten).
--
-- Anwendung:
--   python3 scripts/migrate-norm-aliases.py          # idempotent, mit Log
--   sqlite3 corpus/eudi-nexus.db < scripts/migrate-norm-aliases.sql  # raw
--
-- Rückgängig machen:
--   SQLite unterstützt kein DROP COLUMN vor v3.35 (macOS-Default >= 3.39).
--   Für Rollback: DB aus Backup wiederherstellen oder --rebuild ausführen.
--   Ab SQLite ≥ 3.35:
--     ALTER TABLE norm_aliases DROP COLUMN display_name;
--     ALTER TABLE norm_aliases DROP COLUMN shortname;
--     ALTER TABLE norm_aliases DROP COLUMN abbr;
--     ALTER TABLE norm_aliases DROP COLUMN wki_id;
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Neue Spalten hinzufügen
--    DEFAULT NULL = keine Auswirkung auf bestehende Zeilen.
--    Die Werte werden von build-index.py beim nächsten upsert_norm_aliases()
--    befüllt (für neue Ingests) sowie vom Migrations-Script für den Bestand.

ALTER TABLE norm_aliases ADD COLUMN display_name TEXT DEFAULT NULL;
-- Vollständige, menschenlesbare Bezeichnung inkl. Version und Titel-Auszug.
-- Beispiel: "EN 319 401 v3.3.1 — General Policy Requirements for TSP"
-- Wird von list_norms() direkt zurückgegeben — kein SQL-Subquery mehr nötig.

ALTER TABLE norm_aliases ADD COLUMN shortname TEXT DEFAULT NULL;
-- KI-generierter Langname (schema.md §2), stabil über Versionswechsel.
-- Beispiel: "TrustServicePolicy-Baseline"
-- NULL = noch nicht generiert; list_norms() fällt auf norm zurück.

ALTER TABLE norm_aliases ADD COLUMN abbr TEXT DEFAULT NULL;
-- Kanonische Kurzklammer (schema.md §2).
-- Beispiel: "TSP-Baseline"
-- Taucht in Contributions-Texten auf → Join-Schlüssel für contributions-analyser.

ALTER TABLE norm_aliases ADD COLUMN wki_id TEXT DEFAULT NULL;
-- ETSI Work Item Number (5–7 Ziffern, ohne Prefix "00").
-- Beispiel: "19401" für EN 319 401.
-- Ermöglicht direkten Link zu https://www.etsi.org/work-items?search=<wki_id>
-- und Join mit contributions-analyser.scripts.WKI_ID-Upsert.py.


-- 2. Neue Indizes für häufige Abfragemuster
--    (bestehend: norm_aliases_norm_idx ON norm_aliases(norm))

-- 2a. Lookup via abbr — used by contributions-analyser "affects" JOIN
CREATE INDEX IF NOT EXISTS norm_aliases_abbr_idx
    ON norm_aliases(abbr)
    WHERE abbr IS NOT NULL;

-- 2b. Lookup via wki_id — used by fetch-etsi-work-items.py and WKI_ID-Upsert.py
CREATE INDEX IF NOT EXISTS norm_aliases_wki_idx
    ON norm_aliases(wki_id)
    WHERE wki_id IS NOT NULL;

-- 2c. Covering index: alias_type + norm — used by _resolve_norm() Step 2 filter
--     (WHERE alias_type IN ('etsi_short','etsi_full') is the hot path)
CREATE INDEX IF NOT EXISTS norm_aliases_type_norm_idx
    ON norm_aliases(alias_type, norm);


-- 3. Verifikation (Kommentar — für manuelle Prüfung nach Migration)
--
--   SELECT alias_type, COUNT(*), COUNT(display_name), COUNT(shortname), COUNT(abbr)
--   FROM norm_aliases
--   GROUP BY alias_type
--   ORDER BY alias_type;
--
--   Erwartetes Ergebnis nach erstem build-index.py --rebuild:
--     etsi_full   | N | N | NULL-fill | NULL-fill   ← display_name befüllt, shortname/abbr noch NULL
--     etsi_short  | N | N | N         | NULL-fill
--     esi_key     | N | N | NULL-fill | NULL-fill
--     esi_prefix  | N | N | NULL-fill | NULL-fill
--     wki_id      | N | N | N         | N
--
--   shortname und abbr werden erst nach KI-Generierung (corpus/schema.md §2)
--   durch einen separaten Upsert-Schritt befüllt.
