#!/usr/bin/env sh
# test/enrich-titles-force.sh
# Force full re-extraction + re-generate AI short titles.
set -e
cd "$(dirname "$0")/.."

echo '=== enrich-titles: --force (limit 2) ==='
node scripts/enrich-titles.js --force --limit=2

# Assertions
TITLES_DIR="downloads/specs/_titles"
[ -d "$TITLES_DIR" ] || { echo "FAIL: $TITLES_DIR not found"; exit 1; }

COUNT=$(ls "$TITLES_DIR"/*.title.json 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT" -ge 1 ] || { echo "FAIL: no .title.json files found after --force run"; exit 1; }

# Every written file must have etsiNumber
BAD=$(node -e "
  const fs=require('fs'), path=require('path');
  const dir='$TITLES_DIR';
  const files=fs.readdirSync(dir).filter(f=>f.endsWith('.title.json'));
  const bad=files.filter(f=>{ try{ const r=JSON.parse(fs.readFileSync(path.join(dir,f),'utf-8')); return !r.etsiNumber; }catch{return true;} });
  console.log(bad.length);
")
[ "$BAD" -eq 0 ] || { echo "FAIL: $BAD title.json file(s) missing etsiNumber field"; exit 1; }

echo "PASS: $COUNT records written, all have etsiNumber"
