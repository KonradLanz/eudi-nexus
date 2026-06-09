#!/bin/sh
# Test: --repair-wki-ids
# Primary source: esi_overview.json detailUrl (source of truth)
# Fallback: HTML body WKI_ID (with warning, for sidecars not in overview)
# Never fails if HTML body has no WKI_ID — those are just skipped with a warning
set -e
echo "=== download-specs: --repair-wki-ids ==="
node scripts/download-specs.js --repair-wki-ids

# Assert: no sidecar has wkiId: unknown when its etsiNumber exists in esi_overview.json
FAILED=0
for f in downloads/specs/_workitems/*.workitem.html; do
  [ -f "$f" ] || continue
  if grep -q '<!-- wkiId: unknown -->' "$f"; then
    # Extract etsiNumber from header
    ETSI=$(grep -m1 '<!-- etsiNumber:' "$f" | sed 's/.*etsiNumber: //;s/ -->//')
    # Check if this etsiNumber exists in overview (replace / with _ for filename match is not needed here)
    if node -e "
      const ov = require('./downloads/esi_overview.json');
      const all = [...(ov.activeWorkItems||[]),...(ov.publishedDocuments||[])];
      const found = all.find(x => x.etsiNumber === '$ETSI' && x.detailUrl && x.detailUrl.match(/WKI_ID=\\d+/i));
      process.exit(found ? 1 : 0);
    " 2>/dev/null; then
      : # not in overview with WKI_ID — acceptable
    else
      echo "FAIL: $f still has wkiId: unknown but overview has WKI_ID for $ETSI"
      FAILED=$((FAILED + 1))
    fi
  fi
done

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
echo "PASS: no repairable wkiId: unknown remaining"
echo "  ✓ PASS"
