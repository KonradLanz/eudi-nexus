#!/usr/bin/env sh
# test/enrich-titles-default.sh
# Skip records where shortTitle already exists.
set -e
echo '=== enrich-titles: default (skip complete records) ==='
node scripts/enrich-titles.js --limit=5
