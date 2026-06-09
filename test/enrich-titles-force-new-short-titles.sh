#!/usr/bin/env sh
# test/enrich-titles-force-new-short-titles.sh
# Re-run AI only — keeps extracted fullTitle / etsiShortTitle / PDF data on disk.
# Use for iterating AI short titles without repeating expensive extraction.
set -e
echo '=== enrich-titles: --force-new-short-titles ==='
node scripts/enrich-titles.js --force-new-short-titles --limit=5
