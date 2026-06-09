#!/usr/bin/env sh
# test/enrich-titles-no-ai.sh
# Extraction only — skip AI, useful for debugging title extraction.
set -e
echo '=== enrich-titles: --no-ai ==='
node scripts/enrich-titles.js --no-ai --limit=5
