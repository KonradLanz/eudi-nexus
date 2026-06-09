#!/bin/sh
# Run all test scripts and report results
set -e

PASS=0
FAIL=0

run_test() {
  name="$1"
  script="$2"
  echo
  echo "▶ $name"
  if sh "$script"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  ✗ FAIL: $name"
  fi
}

run_test "download-specs: fresh (cache)"           test/download-specs-fresh.sh
run_test "download-specs: force-update-check"       test/download-specs-force-update-check.sh
run_test "download-specs: force-download"           test/download-specs-force-download.sh
run_test "download-specs: repair-wki-ids"           test/download-specs-repair-wki-ids.sh
run_test "enrich-titles: default"                   test/enrich-titles-default.sh
run_test "enrich-titles: --force"                   test/enrich-titles-force.sh
run_test "enrich-titles: --force-new-short-titles"  test/enrich-titles-force-new-short-titles.sh
run_test "enrich-titles: --no-ai"                   test/enrich-titles-no-ai.sh

echo
echo "================================"
echo "  PASS: $PASS  FAIL: $FAIL"
echo "================================"
[ "$FAIL" -eq 0 ]
