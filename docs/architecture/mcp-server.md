# MCP Server Architecture — eudi-nexus

> Local MCP server that exposes ETSI/IETF spec content to LLM clients
> (Claude Desktop, Cursor, VS Code Copilot, …) via stdio transport.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        LM Studio / Ollama                       │
│          localhost:1234 (OpenAI-compat)  /  localhost:11434     │
│          • Inference models (Gemma-4, Phi-3.5, …)               │
│          • Embedding model (nomic-embed-text  ←  chosen)        │
└────────────────────────┬────────────────────────────────────────┘
                         │ /v1/embeddings
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   scripts/build-index.py               [TODO 1] │
│                                                                  │
│  Input:  corpus/specs/_segments/*.segments.json                 │
│  Output: corpus/eudi-nexus.db  (SQLite, gitignored)             │
│                                                                  │
│  • FTS5 full-text index  (BM25 keyword search)                  │
│  • vec0 virtual table    (cosine similarity via sqlite-vec)     │
│  • Hybrid scoring:  0.6 × BM25  +  0.4 × cosine                │
│  • Metadata columns: norm, version, type, section,              │
│    normative_keywords, anchor (#page=N), profile                │
└────────────────────────┬────────────────────────────────────────┘
                         │ SQLite
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   scripts/mcp-server.py                [TODO 2] │
│                                                                  │
│  Transport:  stdio  (no port, no auth needed locally)           │
│  Framework:  FastMCP (Python)                                   │
│                                                                  │
│  Tools:                                                         │
│    search_norm(query, norm?, section?, type?)                   │
│      → hybrid BM25+cosine, top-K chunks with anchor            │
│                                                                  │
│    get_requirements(norm, section?)                             │
│      → all NORM segments filtered by shall/must/should          │
│                                                                  │
│    cite_clause(segment_id)                                      │
│      → full text + PDF backref "#page=N" + metadata            │
│                                                                  │
│  Resources:                                                     │
│    norms://list   → all indexed norms + version + segment count │
└────────────────────────┬────────────────────────────────────────┘
                         │ stdio JSON-RPC 2.0
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              MCP Client (Claude Desktop / Cursor / …)           │
└─────────────────────────────────────────────────────────────────┘
```

## Pipeline: PDF → MCP

```
downloads/specs/**/*.pdf
        │
        ▼
  pdf-ingest.py          → corpus/specs/*.json
        │                  (pages, section tracking, shortnames)
        ▼
  pdf-segment.py         → corpus/specs/_segments/*.segments.json
        │                  (HEADER/FOOTER stripped, NORM/INFORM/SECTION
        │                   classified, normative_keywords extracted,
        │                   anchor: "#page=N" per block)
        │
        │                → corpus/specs/_adoc/*.adoc
        │                  (AsciiDoc with [[anchor]] backrefs — for humans,
        │                   NOT used by MCP server)
        ▼
  build-index.py         → corpus/eudi-nexus.db     [TODO 1]
        │                  (SQLite FTS5 + sqlite-vec embeddings)
        ▼
  mcp-server.py          ← MCP clients connect here  [TODO 2]
```

## Data Flow: search_norm()

```
Client query: "TSP shall maintain audit logs"
        │
        ├─ BM25 (FTS5):   keyword match on text column
        │
        ├─ Embedding:     query → /v1/embeddings (LM Studio)
        │                 cosine similarity against vec0 table
        │
        └─ Hybrid score:  0.6 × BM25_rank + 0.4 × cosine_sim
                          → top-K segments

Response per segment:
  {
    "id":       "en319401_p42_b3",
    "norm":     "EN 319 401",
    "section":  "7.5",
    "text":     "The TSP shall maintain audit logs …",
    "anchor":   "#page=42",           ← click → PDF page
    "type":     "NORM",
    "keywords": ["shall"]
  }
```

## MCP Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "eudi-nexus": {
      "command": "python3",
      "args": ["/path/to/eudi-nexus/scripts/mcp-server.py"],
      "env": {
        "LMSTUDIO_BASE_URL": "http://localhost:1234"
      }
    }
  }
}
```

### Cursor / VS Code

```json
{
  "mcp": {
    "servers": {
      "eudi-nexus": {
        "type": "stdio",
        "command": "python3",
        "args": ["scripts/mcp-server.py"]
      }
    }
  }
}
```

## Files & Directories

| Path | Purpose | In Git? |
|---|---|---|
| `corpus/specs/*.json` | Raw ingest output (pages) | ✅ |
| `corpus/specs/_segments/*.segments.json` | Classified blocks | ✅ |
| `corpus/specs/_adoc/*.adoc` | AsciiDoc for humans | ✅ |
| `corpus/eudi-nexus.db` | SQLite index | ❌ (gitignored, rebuilt locally) |
| `scripts/pdf-ingest.py` | PDF → corpus JSON | ✅ |
| `scripts/pdf-segment.py` | Corpus JSON → segments | ✅ |
| `scripts/build-index.py` | Segments → SQLite | 🔜 TODO 1 |
| `scripts/mcp-server.py` | MCP server | 🔜 TODO 2 |

## Dependencies (Python)

```
pdfplumber      # already in requirements.txt
fastmcp         # MCP server framework
sqlite-vec      # vector search in SQLite (no Qdrant/Chroma needed)
httpx           # async HTTP for LM Studio /v1/embeddings
```

## Related

- Credential backend: see [`TODO-SECURITY.md`](../../TODO-SECURITY.md)
  and [`bootstrap-foundation/feature/enter-once-cache/CREDENTIAL-BACKENDS.md`](https://github.com/KonradLanz/bootstrap-foundation/blob/feature/enter-once-cache/CREDENTIAL-BACKENDS.md)
- Segment schema: see [`pdf-segment.py`](../../scripts/pdf-segment.py)
- Ingest pipeline: see [`pdf-ingest.py`](../../scripts/pdf-ingest.py)
