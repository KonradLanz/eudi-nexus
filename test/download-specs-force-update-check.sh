#!/usr/bin/env sh
# test/download-specs-force-update-check.sh
# HEAD+ETag check for every cached sidecar — refresh only if changed.
set -e
cd "$(dirname "$0")/.."

echo '=== download-specs: --force-update-check (limit 2) ==='
node scripts/download-specs.js --force-update-check --limit=2

# Assertions
RESULTS="downloads/specs/_download_results.json"
[ -f "$RESULTS" ] || { echo "FAIL: $RESULTS not found"; exit 1; }

# At least one workitem sidecar should exist after this run
WI_COUNT=$(ls downloads/specs/_workitems/*.workitem.html 2>/dev/null | wc -l | tr -d ' ')
[ "$WI_COUNT" -ge 1 ] || { echo "FAIL: no workitem sidecars found in downloads/specs/_workitems/"; exit 1; }

echo "PASS: results file present, $WI_COUNT workitem sidecar(s) found"
