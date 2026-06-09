#!/usr/bin/env sh
# test/download-specs-fresh.sh
# Default run — skip everything that is already cached.
set -e
cd "$(dirname "$0")/.."

echo '=== download-specs: default (cache only) ==='
node scripts/download-specs.js --limit=3

# Assertions
RESULTS="downloads/specs/_download_results.json"
[ -f "$RESULTS" ] || { echo "FAIL: $RESULTS not found"; exit 1; }

COUNT=$(node -e "const r=require('./$RESULTS'); console.log((r.success||[]).length+(r.skipped||[]).length+(r.redownloaded||[]).length)")
[ "$COUNT" -ge 1 ] || { echo "FAIL: expected at least 1 result entry, got $COUNT"; exit 1; }

echo "PASS: $COUNT entries in results (success+skipped+redownloaded)"
