# MCP Tools — EUDI-Nexus

This document describes the five MCP tools exposed by `scripts/mcp-server.py`.
It is the canonical reference for prompt-engineering, model fine-tuning tests,
and onboarding new LLM agents.

---

## Overview

| Tool | Purpose | When to call |
|---|---|---|
| `list_norms` | Enumerate all indexed norms with metadata | **First step** when unsure which norm to target |
| `get_toc` | Table of contents (section headings) for one norm | Before `get_section` — orient yourself in the structure |
| `search_norm` | Hybrid BM25 + semantic search across segments | Main search tool for any content query |
| `get_section` | All segments of a specific section | Deep-read after search returns a truncated result |
| `get_segment` | Single segment by exact ID | Follow-up if you already have a segment ID |

**Canonical workflow:**

```
list_norms          # find the right norm + exact 'norm' field value
  └─► get_toc       # optional: see section structure before searching
       └─► search_norm  # search by content
            └─► get_section  # full context for a section
                 └─► get_segment  # single segment by ID (rarely needed)
```

---

## Norm Identification

### How norms are stored

The corpus may contain two classes of norm keys depending on the ingest path:

| Class | Example DB value | Human name | Source |
|---|---|---|---|
| Human-readable | `EN 319 401` | EN 319 401 | Correctly ingested ✅ |
| ESI docbox key | `ESI-0019401v331v322` | EN 319 401 v3.3.1 | Legacy ingest ⚠️ |

`list_norms` automatically decodes ESI docbox keys and exposes the human-readable
name in `display_name` and `etsi_norm`.  **Always copy the raw `norm` field** (not
`display_name`) into `search_norm` / `get_section` / `get_toc`.

### Fuzzy resolver (`_resolve_norm`)

All tools that accept a `norm` parameter run it through a fuzzy resolver before
hitting the DB.  This means you do **not** need to pass the exact DB key —
the resolver handles:

| Input variant | Resolves to | Mechanism |
|---|---|---|
| `"EN 319 401"` | `EN 319 401` | direct LIKE match |
| `"319401"` | `EN 319 401` or `ESI-0019401…` | compact-number LIKE |
| `"319 401"` | `EN 319 401` | digit-group LIKE |
| `"eidas"` | `EN 319 401`, `TS 119 612`, … | alias table |
| `"mdl"` | `ISO/IEC 18013-5` | alias table |
| `"openid4vp"` | `OpenID4VP` | alias table |
| `"sd-jwt"` | `SD-JWT` | alias table |
| `"ESI-0019401v331v322"` | `ESI-0019401v331v322` | literal passthrough |

**Important:** if the corpus still contains ESI docbox keys and you pass
`"EN 319 401"`, the LIKE `%EN 319 401%` will **not** match `ESI-0019401v331v322`.
In that case:
1. Call `list_norms` — the response includes `etsi_norm` on ESI entries.
2. Pass the raw `norm` field value (e.g. `"ESI-0019401v331v322"`) directly.

The permanent fix is to re-ingest with the corrected `pdf-ingest.py` which
normalises ESI docbox keys to human-readable form at index time.

---

## Tool Reference

### `list_norms`

```
list_norms()  →  { total_norms, total_segments, norms[] }
```

**Parameters:** none — call with no arguments.

**Returns:** `norms` list where each entry contains:

| Field | Type | Description |
|---|---|---|
| `norm` | string | **Exact value to pass to other tools** |
| `version` | string | Latest indexed version, e.g. `"v3.3.1"` |
| `display_name` | string | Human-readable label: `"EN 319 401 v3.3.1 — General Policy…"` |
| `etsi_norm` | string? | Only present when `norm` is an ESI docbox key; decoded human name |
| `title` | string? | Document title from section 0 |
| `total_segments` | int | All segments |
| `norm_count` | int | Normative (SHALL/MUST) segments |
| `inform_count` | int | Informative segments |
| `section_count` | int | Section-heading segments |
| `embedded_count` | int | Segments with a semantic embedding |

**Usage note:** Always call `list_norms` first when unsure which norm to target.
Copy the exact `norm` field — not `display_name` — into `search_norm` or `get_section`.

---

### `get_toc`

```
get_toc(norm, version?, depth?)  →  { norm, version, depth, section_count, toc[] }
```

**Parameters:**

| Param | Required | Type | Description |
|---|---|---|---|
| `norm` | ✅ | string | Norm identifier — fuzzy matching applies (see above) |
| `version` | ✗ | string | Exact version, e.g. `"v3.3.1"`. Omit = most recent. |
| `depth` | ✗ | int 1-5 | Max section-number depth. Default 3. Use 1-2 for a quick overview. |

**Returns:** `toc` — ordered list of `{ section, title }` dicts.

**Workflow:**
```
get_toc("EN 319 401", depth=2)
  → [{"section": "5", "title": "General requirements"},
     {"section": "5.1", "title": "TSP practice and policy statements"}, …]
```
Use the section numbers from `toc` as input to `get_section`.

---

### `search_norm`

```
search_norm(query, norm?, version?, limit?, alpha?, types?)
  →  { query, result_count, mode, embedding_backend, results[] }
```

**Parameters:**

| Param | Required | Type | Default | Description |
|---|---|---|---|---|
| `query` | ✅ | string | — | Search text. Be specific — use technical domain terms. |
| `norm` | ✗ | string | all norms | Filter to one norm. Fuzzy matching applies. |
| `version` | ✗ | string | all | Exact version string. |
| `limit` | ✗ | int 1-20 | 10 | Result count. Use 20 for exploratory queries. |
| `alpha` | ✗ | float 0-1 | 0.5 | BM25 ↔ semantic weight. See table below. |
| `types` | ✗ | string[] | both | `["NORM"]` = SHALL/MUST only; `["INFORM"]` = informative only. |

**`alpha` guide:**

| `alpha` | Mode | Best for |
|---|---|---|
| `1.0` | BM25 only | Known section numbers, exact terms |
| `0.5` | Balanced hybrid (default) | Most queries |
| `0.2` | Semantic-heavy | Broad / conceptual queries like "TSP obligations" |
| `0.0` | Cosine only | Requires embedding backend |

**Query quality:**
- ✅ Good: `"audit log confidentiality integrity UTC synchronisation"`
- ✅ Good: `"termination plan private key destruction notification"`
- ⚠️ Weak: `"trust service provider requirements"` — too generic, BM25 suffers; use `alpha=0.2`

**If `result_count = 0`:**
1. Broaden the query.
2. Lower `alpha` to `0.2`.
3. Omit `norm` filter to search all norms.
4. Call `list_norms` and verify the `norm` field value.

---

### `get_section`

```
get_section(norm, section?, version?, types?)  →  { norm, version, section, segment_count, segments[] }
```

Section matching uses **prefix logic**: `"5"` returns 5, 5.1, 5.1.1, 5.2, …

**Parameters:**

| Param | Required | Type | Description |
|---|---|---|---|
| `norm` | ✅ | string | Norm identifier — fuzzy matching applies. |
| `section` | ✗ | string | Section number, e.g. `"5"`, `"5.1"`, `"7.10"`. Omit = full norm. |
| `version` | ✗ | string | Exact version. Omit = most recent. |
| `types` | ✗ | string[] | `["NORM"]`, `["INFORM"]`, `["SECTION"]`, or omit for all. |

---

### `get_segment`

```
get_segment(segment_id)  →  segment dict  |  { "error": "not found" }
```

**Parameters:**

| Param | Required | Type | Description |
|---|---|---|---|
| `segment_id` | ✅ | string | Exact segment ID, e.g. `"en319401_p5_b2"`. Obtain from `search_norm` results. |

Rarely needed directly — use `get_section` to get full section context instead.

---

## Known Limitations

### ESI docbox keys in legacy corpus

Corpora ingested before the `pdf-ingest.py` normalisation fix may store norms
as `ESI-00NNNNN…` docbox keys.  The resolver's LIKE-match cannot map
`"EN 319 401"` → `"ESI-0019401v331v322"` because the human name does not appear
in the DB key.

**Workaround (model-side):**
1. Call `list_norms`.
2. Look for the `etsi_norm` field on the entry you need.
3. Pass the raw `norm` field to subsequent calls.

**Permanent fix:** re-ingest with the corrected pipeline so norm keys are stored
as human-readable strings from the start.

### Fuzzy matching scope

The alias table covers common EUDI-ecosystem shorthands (`eidas`, `mdl`,
`openid4vp`, `sd-jwt`, `haip`, …).  Newly published norms or version-specific
shorthands may not be in the alias table yet.  Always fall back to `list_norms`
when a fuzzy lookup yields no results.

---

## LM Studio / Ollama — Embedding Backend

Semantic (cosine) search requires a running embedding backend:

| Backend | Detection | Env var |
|---|---|---|
| LM Studio | `http://localhost:1234/v1/embeddings` | `EMBEDDING_BACKEND=lmstudio` |
| Ollama | `http://localhost:11434/api/embeddings` | `EMBEDDING_BACKEND=ollama` |
| None | auto-detect falls back to BM25-only | — |

When no backend is available, `search_norm` runs in `bm25_only` mode
(`mode` field in response).  Set `alpha=1.0` explicitly to avoid misleading
`"no embeddings"` warnings.

---

## Automating Model Calls

### Local inference (LM Studio / Ollama)

For batch evaluation against Gemma or any local model, the MCP server runs as a
stdio process.  The simplest automation loop:

```python
import subprocess, json

proc = subprocess.Popen(
    ["python", "scripts/mcp-server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
)

def call_tool(name: str, args: dict) -> dict:
    msg = json.dumps({"tool": name, "arguments": args}) + "\n"
    proc.stdin.write(msg)
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())
```

### Yoyo / remote inference

If local inference is slow, routing calls to a faster remote host ("yoyo") is
straightforward — swap the embedding endpoint env var and point the model client
at the remote IP.  The MCP server itself stays local; only the embedding
backend changes.

```bash
EMBEDDING_BACKEND=lmstudio \
EMBEDDING_BASE_URL=http://yoyo.local:1234/v1 \
python scripts/mcp-server.py
```

---

*Last updated: 2026-06-15*
