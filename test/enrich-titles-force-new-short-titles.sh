#!/usr/bin/env sh
# test/enrich-titles-force-new-short-titles.sh
# Re-run AI only — keep extracted titles, only regenerate shortTitle.
set -e
cd "$(dirname "$0")/.."

echo '=== enrich-titles: --force-new-short-titles (limit 2) ==='
node scripts/enrich-titles.js --force-new-short-titles --limit=2

# Assertions
TITLES_DIR="downloads/specs/_titles"
[ -d "$TITLES_DIR" ] || { echo "FAIL: $TITLES_DIR not found"; exit 1; }

COUNT=$(ls "$TITLES_DIR"/*.title.json 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT" -ge 1 ] || { echo "FAIL: no .title.json files found"; exit 1; }

echo "PASS: $COUNT .title.json file(s) present after AI refresh"
