#!/bin/sh
# test/enrich-titles-full-ai-pass.sh
#
# Complete AI pass over ALL workitem sidecars.
# Intended to be run with a local LM Studio / Ollama model loaded (e.g. Gemma 4 31B Q4_K_M).
#
# What it checks per .title.json written in this run:
#   - shortTitle is non-empty
#   - shortTitle is ≤4 words
#   - shortTitle is not a dummy ("Title 1", "null", "N/A", "unknown", ...)
#   - shortTitleSource is 'ai'
#   - corpus/specs JSON was patched: shortTitleAI field matches shortTitle
#
# Exits 1 on any hard failure.
# Prints a summary table of all AI suggestions at the end.
set -e
cd "$(dirname "$0")/.."

echo "=== enrich-titles: full AI pass (all sidecars) ==="
echo ""

# Verify AI is reachable before starting
AI_OK=$(node -e "
import('../src/local-ai.js').then(async m => {
  const ok = await m.isAvailable();
  const model = ok ? await m.bestModel() : '';
  const prov  = ok ? await m.activeProvider() : '';
  process.stdout.write(ok ? '1:' + prov + ':' + model : '0::');
}).catch(() => process.stdout.write('0::'));
" 2>/dev/null)

AI_AVAIL=$(echo "$AI_OK" | cut -d: -f1)
AI_PROV=$(echo "$AI_OK"  | cut -d: -f2)
AI_MODEL=$(echo "$AI_OK" | cut -d: -f3)

if [ "$AI_AVAIL" != "1" ]; then
  echo "SKIP: No local AI available — start LM Studio or Ollama first"
  echo "      Then re-run: sh test/enrich-titles-full-ai-pass.sh"
  exit 0
fi

echo "  AI provider : $AI_PROV"
echo "  Model       : $AI_MODEL"
echo ""

RUN_START=$(node -e "process.stdout.write(new Date().toISOString())")

# Full pass: --force-new-short-titles regenerates AI for all existing sidecars
# without re-parsing HTML (fast). Add --force to also re-extract titles.
node scripts/enrich-titles.js --force-new-short-titles

echo ""
echo "--- Quality check ---"

FAILED=0
TOTAL=0
SKIPPED=0
WARN=0

DUMMY_RE="^(null|n/a|unknown|untitled|title [0-9]|tbd|none)$"

for f in downloads/specs/_titles/*.title.json; do
  [ -f "$f" ] || continue

  # Only check records written in this run
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

  ETSI=$(node -e "const r=require('./$f'); process.stdout.write(r.etsiNumber||'');" 2>/dev/null)
  SHORT=$(node -e "const r=require('./$f'); process.stdout.write(r.shortTitle||'');" 2>/dev/null)
  SOURCE=$(node -e "const r=require('./$f'); process.stdout.write(r.shortTitleSource||'');" 2>/dev/null)

  # shortTitle must be non-empty
  if [ -z "$SHORT" ]; then
    echo "FAIL: $ETSI — shortTitle is empty"
    FAILED=$((FAILED + 1))
    continue
  fi

  # shortTitleSource must be 'ai'
  if [ "$SOURCE" != "ai" ]; then
    echo "WARN: $ETSI — shortTitleSource='$SOURCE' (expected 'ai')"
    WARN=$((WARN + 1))
  fi

  # shortTitle must be ≤4 words
  WORD_COUNT=$(echo "$SHORT" | wc -w | tr -d ' ')
  if [ "$WORD_COUNT" -gt 4 ]; then
    echo "WARN: $ETSI — shortTitle has $WORD_COUNT words: \"$SHORT\""
    WARN=$((WARN + 1))
  fi

  # shortTitle must not be a dummy value
  SHORT_LOWER=$(echo "$SHORT" | tr '[:upper:]' '[:lower:]')
  if echo "$SHORT_LOWER" | grep -qiE "$DUMMY_RE"; then
    echo "FAIL: $ETSI — shortTitle is a dummy value: \"$SHORT\""
    FAILED=$((FAILED + 1))
    continue
  fi

  # Corpus patch check: if corpus JSON exists, shortTitleAI must match shortTitle
  CORPUS_AI=$(node -e "
    const path = require('path');
    const fs   = require('fs');
    const etsi = '$ETSI'.replace(/[\\s-]/g, '').toUpperCase();
    const dir  = 'corpus/specs';
    if (!fs.existsSync(dir)) { process.stdout.write('NO_CORPUS'); process.exit(0); }
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.json'));
    for (const f of files) {
      try {
        const r = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
        const hay = (r.norm || '').replace(/[\\s-]/g, '').toUpperCase();
        if (hay === etsi) { process.stdout.write(r.shortTitleAI || 'MISSING'); process.exit(0); }
      } catch {}
    }
    process.stdout.write('NOT_FOUND');
  " 2>/dev/null)

  if [ "$CORPUS_AI" = "MISSING" ]; then
    echo "WARN: $ETSI — corpus JSON exists but shortTitleAI not set"
    WARN=$((WARN + 1))
  elif [ "$CORPUS_AI" != "NO_CORPUS" ] && [ "$CORPUS_AI" != "NOT_FOUND" ] && [ "$CORPUS_AI" != "$SHORT" ]; then
    echo "WARN: $ETSI — corpus shortTitleAI mismatch: \"$CORPUS_AI\" vs \"$SHORT\""
    WARN=$((WARN + 1))
  fi

  echo "  ✓  $ETSI — \"$SHORT\""
done

echo ""
echo "=== Summary ==="
echo "  Provider : $AI_PROV"
echo "  Model    : $AI_MODEL"
echo "  Checked  : $TOTAL fresh records"
echo "  Skipped  : $SKIPPED pre-existing"
echo "  Warnings : $WARN"
echo "  Failures : $FAILED"

if [ "$FAILED" -gt 0 ]; then
  echo ""
  echo "FAIL"
  exit 1
fi

if [ "$TOTAL" -eq 0 ]; then
  echo "WARN: 0 fresh records checked — all sidecars may have been cached"
  echo "      Re-run with: node scripts/enrich-titles.js --force-new-short-titles"
fi

echo ""
echo "  ✓ PASS"
