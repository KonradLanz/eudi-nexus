#!/bin/sh
# Test: --repair-wki-ids — no login, no download, only repairs wkiId: unknown in existing sidecars
set -e
echo "=== download-specs: --repair-wki-ids ==="
node scripts/download-specs.js --repair-wki-ids

# Assert: no workitem sidecar has wkiId: unknown anymore (if any WKI_ID exists in HTML body)
UNKNOWN=$(grep -rl '<!-- wkiId: unknown -->' downloads/specs/_workitems/ 2>/dev/null | while read f; do
  if grep -q 'WKI_ID=' "$f"; then echo "$f"; fi
done | wc -l | tr -d ' ')
if [ "$UNKNOWN" -gt 0 ]; then
  echo "FAIL: $UNKNOWN sidecar(s) still have wkiId: unknown despite WKI_ID present in HTML"
  exit 1
fi
echo "PASS: no repairable wkiId: unknown remaining"
echo "  ✓ PASS"
