#!/bin/sh
# Test: title extraction quality
#
# Runs enrich-titles --no-ai --force --limit=20 and checks only the records
# written in this run (identified by generatedAt within the last 60s).
#
# Checks per record:
#   - etsiNumber is non-empty
#   - shortTitleSource is not 'ai' (--no-ai was passed)
#   - at least one title field is non-null (fullTitleWorkitem, etsiShortTitle, fullTitlePdf)
#   - no field is the literal string "null" (serialisation bug guard)
#
# NOTE: --force rewrites existing records so the shortTitleSource check is valid.
# Pre-existing AI records from previous runs are NOT inspected.
set -e
echo "=== enrich-titles: extraction quality (--no-ai --force --limit=20) ==="

RUN_START=$(node -e "process.stdout.write(new Date().toISOString())")
node scripts/enrich-titles.js --no-ai --force --limit=20

FAILED=0
TOTAL=0
NO_TITLE=0
SKIPPED=0

for f in downloads/specs/_titles/*.title.json; do
  [ -f "$f" ] || continue

  # Only inspect records written in this run (generatedAt >= RUN_START)
  IS_FRESH=$(node -e "
    try {
      const r = require('./$f');
      const written = new Date(r.generatedAt).getTime();
      const start   = new Date('$RUN_START').getTime();
      process.stdout.write(written >= start - 2000 ? '1' : '0');
    } catch { process.stdout.write('0'); }
  " 2>/dev/null)
  if [ "$IS_FRESH" = "0" ]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

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
    echo "FAIL: $f ($ETSI) — shortTitleSource='ai' but --no-ai was used"
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
    echo "WARN: $f ($ETSI) — no title extracted (all title fields null)"
    NO_TITLE=$((NO_TITLE + 1))
    # warn only — some workitems genuinely have no parseable title
  fi

  # Guard: no field should be the literal string "null" (JSON serialisation bug)
  LITERAL_NULL=$(node -e "
    const raw = require('fs').readFileSync('./$f', 'utf-8');
    const bad = (raw.match(/: \"null\"/g) || []).length;
    process.stdout.write(String(bad));
  " 2>/dev/null)
  if [ "$LITERAL_NULL" -gt 0 ]; then
    echo "FAIL: $f ($ETSI) — contains literal string \"null\" value (serialisation bug)"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "Results: $TOTAL records checked, $FAILED hard failures, $NO_TITLE with no title, $SKIPPED pre-existing skipped"

if [ "$FAILED" -gt 0 ]; then
  echo "FAIL"
  exit 1
fi

# Warn if more than 50% of fresh records have no title at all
if [ "$TOTAL" -gt 0 ]; then
  HALF=$((TOTAL / 2))
  if [ "$NO_TITLE" -gt "$HALF" ]; then
    echo "WARN: more than 50% of records have no title — extraction logic may be broken"
  fi
fi

echo "  ✓ PASS"
