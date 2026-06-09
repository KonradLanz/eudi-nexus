#!/usr/bin/env sh
# test/enrich-titles-default.sh
# Default run — skip sidecars that already have a shortTitle.
set -e
cd "$(dirname "$0")/.."

echo '=== enrich-titles: default (limit 3) ==='
node scripts/enrich-titles.js --limit=3

# Assertions
TITLES_DIR="downloads/specs/_titles"
[ -d "$TITLES_DIR" ] || { echo "FAIL: $TITLES_DIR directory not found"; exit 1; }

COUNT=$(ls "$TITLES_DIR"/*.title.json 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT" -ge 1 ] || { echo "FAIL: no .title.json files found in $TITLES_DIR"; exit 1; }

echo "PASS: $COUNT .title.json file(s) present"
