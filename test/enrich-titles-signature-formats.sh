#!/usr/bin/env sh
# test/enrich-titles-signature-formats.sh
#
# Checks that ETSI signature format specs (XAdES, CAdES, PAdES, JAdES)
# get correct title enrichment.
#
# EN 319 122  = CAdES (CMS Advanced Electronic Signatures)
# EN 319 132  = XAdES (XML Advanced Electronic Signatures)
# EN 319 142  = PAdES (PDF Advanced Electronic Signatures)
# TS 119 182  = JAdES (JSON Advanced Electronic Signatures)
#
# Expected shortTitleAI results (examples):
#   CAdES  → "CAdES Signature Format"
#   XAdES  → "XAdES Signature Format"
#   PAdES  → "PAdES Signature Format"
#   JAdES  → "JAdES Signature Format"

set -e
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
SKIP=0

check_title() {
  local label="$1"   # e.g. "PAdES Part 2"
  local stem="$2"    # e.g. "EN_319_142-2"
  local expect="$3"  # substring expected in shortTitleAI, case-insensitive

  local json="downloads/specs/_titles/${stem}.title.json"

  if [ ! -f "$json" ]; then
    echo "  ⏭  SKIP  ${label} — no _titles sidecar (workitem may not be downloaded yet)"
    SKIP=$((SKIP + 1))
    return
  fi

  local val
  val=$(node -e "const r=require('fs').readFileSync('$json','utf-8');const d=JSON.parse(r);console.log(d.shortTitleAI||d.shortTitle||'')" 2>/dev/null || echo '')

  if [ -z "$val" ]; then
    echo "  ⚠️  SKIP  ${label} — sidecar exists but no shortTitleAI yet (run enrich-titles first)"
    SKIP=$((SKIP + 1))
    return
  fi

  # Case-insensitive substring check
  local lower_val lower_expect
  lower_val=$(echo "$val"   | tr '[:upper:]' '[:lower:]')
  lower_expect=$(echo "$expect" | tr '[:upper:]' '[:lower:]')

  if echo "$lower_val" | grep -q "$lower_expect"; then
    echo "  ✅  PASS  ${label} → \"${val}\""
    PASS=$((PASS + 1))
  else
    echo "  ❌  FAIL  ${label} → \"${val}\" (expected to contain \"${expect}\")"
    FAIL=$((FAIL + 1))
  fi
}

check_workitem_exists() {
  local label="$1"
  local stem="$2"
  local html="downloads/specs/_workitems/${stem}.workitem.html"
  if [ ! -f "$html" ]; then
    echo "  ⚠️  MISSING workitem sidecar: ${stem}.workitem.html  (${label} — run npm run download)"
    SKIP=$((SKIP + 1))
  fi
}

echo ""
echo "🔏  Signature Format Title Enrichment Test"
echo "==========================================="
echo ""

echo "📋  Checking workitem sidecars exist:"
check_workitem_exists "CAdES Part 1" "EN_319_122-1"
check_workitem_exists "CAdES Part 2" "EN_319_122-2"
check_workitem_exists "XAdES Part 1" "EN_319_132-1"
check_workitem_exists "XAdES Part 2" "EN_319_132-2"
check_workitem_exists "PAdES Part 1" "EN_319_142-1"
check_workitem_exists "PAdES Part 2" "EN_319_142-2"
check_workitem_exists "JAdES"        "TS_119_182-1"
echo ""

echo "🏷️   Checking shortTitleAI values:"
check_title "CAdES Part 1" "EN_319_122-1" "cades"
check_title "CAdES Part 2" "EN_319_122-2" "cades"
check_title "XAdES Part 1" "EN_319_132-1" "xades"
check_title "XAdES Part 2" "EN_319_132-2" "xades"
check_title "PAdES Part 1" "EN_319_142-1" "pades"
check_title "PAdES Part 2" "EN_319_142-2" "pades"
check_title "JAdES"        "TS_119_182-1" "jades"
echo ""

echo "📊  Results: ✅ ${PASS} passed  ❌ ${FAIL} failed  ⏭  ${SKIP} skipped"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "💡  Re-run enrich-titles for affected specs:"
  echo "       npm run enrich-titles -- --force --limit=10"
  exit 1
fi

if [ "$PASS" -eq 0 ] && [ "$FAIL" -eq 0 ]; then
  echo "💡  No specs enriched yet. Download workitems first:"
  echo "       npm run download"
  echo "    Then run title enrichment:"
  echo "       npm run enrich-titles"
fi

exit 0
