# Test Scripts

POSIX shell scripts that exercise each CLI flag combination.
All scripts use `--limit=N` to keep runs fast.

## download-specs.js

| Script | Flag | Description |
|---|---|---|
| `download-specs-fresh.sh` | _(none)_ | Default: skip everything cached, no network for sidecars |
| `download-specs-force-update-check.sh` | `--force-update-check` | HEAD + ETag; refresh only on 200 — escalation level 1 |
| `download-specs-force-download.sh` | `--force-download` | Full GET always; defined clean state — escalation level 2 |

## enrich-titles.js

| Script | Flag | Description |
|---|---|---|
| `enrich-titles-default.sh` | _(none)_ | Skip records where `shortTitle` already set |
| `enrich-titles-force-new-short-titles.sh` | `--force-new-short-titles` | Re-run AI only; extracted data (fullTitle, etsiShortTitle, PDF) preserved |
| `enrich-titles-force.sh` | `--force` | Full re-run: re-extract + re-run AI |
| `enrich-titles-no-ai.sh` | `--no-ai` | Extraction only, no AI call (debug) |

## Usage

```sh
# make executable once
chmod +x test/*.sh

# run individual test
sh test/download-specs-force-download.sh
sh test/enrich-titles-force-new-short-titles.sh
```
