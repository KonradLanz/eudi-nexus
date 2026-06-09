#!/usr/bin/env sh
# test/download-specs-fresh.sh
# Default run — skip everything that is already cached. No network for sidecars.
set -e
echo '=== download-specs: default (cache only) ==='
node scripts/download-specs.js --limit=3
