#!/usr/bin/env sh
# test/run-all.sh
# Run all test scripts in sequence. Stops on first failure (set -e).
# Usage: sh test/run-all.sh
set -e
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
SKIPPED=0

run_test() {
  LABEL="$1"
  SCRIPT="$2"
  echo ""
  echo "\033[1m▶ $LABEL\033[0m"
  if sh "$SCRIPT"; then
    PASS=$((PASS + 1))
    echo "\033[32m  ✓ PASS\033[0m"
  else
    FAIL=$((FAIL + 1))
    echo "\033[31m  ✗ FAIL\033[0m"
  fi
}

# ── download-specs ──────────────────────────────────────────────────────────
run_test "download-specs: fresh (cache)"        test/download-specs-fresh.sh
run_test "download-specs: force-update-check"   test/download-specs-force-update-check.sh
run_test "download-specs: force-download"       test/download-specs-force-download.sh

# ── enrich-titles ───────────────────────────────────────────────────────────
run_test "enrich-titles: default"               test/enrich-titles-default.sh
run_test "enrich-titles: --force"               test/enrich-titles-force.sh
run_test "enrich-titles: --force-new-short-titles" test/enrich-titles-force-new-short-titles.sh
run_test "enrich-titles: --no-ai"               test/enrich-titles-no-ai.sh

# ── summary ─────────────────────────────────────────────────────────────────
echo ""
echo "================================"
echo "  PASS: $PASS  FAIL: $FAIL"
echo "================================"
[ "$FAIL" -eq 0 ] || exit 1
