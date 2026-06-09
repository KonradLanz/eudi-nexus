#!/usr/bin/env sh
# test/download-specs-force-download.sh
# Full GET every time — ignores ETag/TTL, defined clean state.
# Escalation level 2 — use when something went wrong.
set -e
echo '=== download-specs: --force-download ==='
node scripts/download-specs.js --force-download --limit=3
