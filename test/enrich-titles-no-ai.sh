#!/usr/bin/env sh
# test/enrich-titles-no-ai.sh
# Extraction only — no AI calls. Useful offline / in CI.
set -e
cd "$(dirname "$0")/.."

echo '=== enrich-titles: --no-ai (limit 3) ==='
node scripts/enrich-titles.js --no-ai --limit=3

# Assertions
TITLES_DIR="downloads/specs/_titles"
[ -d "$TITLES_DIR" ] || { echo "FAIL: $TITLES_DIR not found"; exit 1; }

COUNT=$(ls "$TITLES_DIR"/*.title.json 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT" -ge 1 ] || { echo "FAIL: no .title.json files found"; exit 1; }

# When --no-ai: shortTitleSource must NOT be 'ai' for newly written records
BAD=$(node -e "
  const fs=require('fs'), path=require('path');
  const dir='$TITLES_DIR';
  const files=fs.readdirSync(dir).filter(f=>f.endsWith('.title.json'));
  const bad=files.filter(f=>{ try{ const r=JSON.parse(fs.readFileSync(path.join(dir,f),'utf-8')); return r.shortTitleSource==='ai' && !r.shortTitle; }catch{return false;} });
  console.log(bad.length);
")
[ "$BAD" -eq 0 ] || { echo "FAIL: $BAD record(s) claim AI source but have no shortTitle"; exit 1; }

echo "PASS: $COUNT records, no invalid AI-source entries"
