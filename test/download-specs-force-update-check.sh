#!/usr/bin/env sh
# test/download-specs-force-update-check.sh
# HEAD + ETag for every sidecar; refresh only if server returns 200.
# Escalation level 1 — sanity-check without a full download.
set -e
echo '=== download-specs: --force-update-check ==='
node scripts/download-specs.js --force-update-check --limit=3
