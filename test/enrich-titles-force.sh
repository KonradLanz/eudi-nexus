#!/usr/bin/env sh
# test/enrich-titles-force.sh
# Full re-run: re-extract titles + re-run AI. Cleans everything.
set -e
echo '=== enrich-titles: --force (full re-run) ==='
node scripts/enrich-titles.js --force --limit=5
