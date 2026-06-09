#!/usr/bin/env sh
# test/download-specs-force-download.sh
# Full re-download — clean state, ignores ETag/TTL.
set -e
cd "$(dirname "$0")/.."

echo '=== download-specs: --force-download (limit 2) ==='
node scripts/download-specs.js --force-download --limit=2

# Assertions
RESULTS="downloads/specs/_download_results.json"
[ -f "$RESULTS" ] || { echo "FAIL: $RESULTS not found"; exit 1; }

REDOWN=$(node -e "const r=require('./$RESULTS'); console.log((r.redownloaded||[]).length)")
SUCCESS=$(node -e "const r=require('./$RESULTS'); console.log((r.success||[]).length)")
TOTAL=$((REDOWN + SUCCESS))
[ "$TOTAL" -ge 1 ] || { echo "FAIL: expected at least 1 (re)downloaded file, got $TOTAL"; exit 1; }

echo "PASS: $SUCCESS new + $REDOWN re-downloaded"
