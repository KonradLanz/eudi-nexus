#!/bin/sh
# Test: title extraction quality
#
# Checks that enrich-titles --no-ai --limit=20 produces valid .title.json files:
#   - file exists
#   - etsiNumber is non-empty
#   - at least one title field is non-null (fullTitleWorkitem, etsiShortTitle, fullTitlePdf)
#   - shortTitleSource is not 'ai' (since --no-ai)
#   - no title field is the literal string "null" (serialisation bug guard)
#
# Does NOT test AI short title quality (see enrich-titles-force-new-short-titles.sh)
set -e
echo "=== enrich-titles: extraction quality (--no-ai --limit=20) ==="
node scripts/enrich-titles.js --no-ai --limit=20

FAILED=0
TOTAL=0
NO_TITLE=0

for f in downloads/specs/_titles/*.title.json; do
  [ -f "$f" ] || continue
  TOTAL=$((TOTAL + 1))

  # etsiNumber must be present and non-empty
  ETSI=$(node -e "const r=require('./$f'); process.stdout.write(r.etsiNumber||'');" 2>/dev/null)
  if [ -z "$ETSI" ]; then
    echo "FAIL: $f — etsiNumber missing or empty"
    FAILED=$((FAILED + 1))
    continue
  fi

  # shortTitleSource must not be 'ai' when run with --no-ai
  SOURCE=$(node -e "const r=require('./$f'); process.stdout.write(r.shortTitleSource||'');" 2>/dev/null)
  if [ "$SOURCE" = "ai" ]; then
    echo "FAIL: $f — shortTitleSource='ai' but --no-ai was used"
    FAILED=$((FAILED + 1))
    continue
  fi

  # At least one title field must be non-null
  HAS_TITLE=$(node -e "
    const r = require('./$f');
    const ok = !!(r.fullTitleWorkitem || r.etsiShortTitle || r.fullTitlePdf);
    process.stdout.write(ok ? '1' : '0');
  " 2>/dev/null)
  if [ "$HAS_TITLE" = "0" ]; then
    echo "WARN: $f ($ETSI) — no title extracted (fullTitleWorkitem, etsiShortTitle, fullTitlePdf all null)"
    NO_TITLE=$((NO_TITLE + 1))
    # warn only, not a hard failure — some workitems genuinely have no parseable title
  fi

  # Guard: no field should be the literal string "null" (JSON serialisation bug)
  LITERAL_NULL=$(node -e "
    const raw = require('fs').readFileSync('./$f', 'utf-8');
    const bad = (raw.match(/: \"null\"/g) || []).length;
    process.stdout.write(String(bad));
  " 2>/dev/null)
  if [ "$LITERAL_NULL" -gt 0 ]; then
    echo "FAIL: $f — contains literal string \"null\" value (serialisation bug)"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "Results: $TOTAL records checked, $FAILED hard failures, $NO_TITLE with no title"

if [ "$FAILED" -gt 0 ]; then
  echo "FAIL"
  exit 1
fi

# Warn if more than 50% have no title at all (extraction may be broken)
if [ "$TOTAL" -gt 0 ]; then
  HALF=$((TOTAL / 2))
  if [ "$NO_TITLE" -gt "$HALF" ]; then
    echo "WARN: more than 50% of records have no title — extraction logic may be broken"
    # not a hard failure, but visible
  fi
fi

echo "  ✓ PASS"
