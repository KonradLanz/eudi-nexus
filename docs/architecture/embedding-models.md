# Embedding Model Decision — eudi-nexus

## Candidates

| | `nomic-embed-text` | `mxbai-embed-large` |
|---|---|---|
| **Parameters** | 137 M | 335 M |
| **Model size** | 274 MB | 670 MB |
| **Dimensions** | 768 (flexibel: 64–768 via MRL) | 1 024 (fest) |
| **Context window** | **8 192 tokens** | 512 tokens |
| **MTEB Score** | ~62 | ~64 |
| **Retrieval (kurze Fragen)** | besser | schlechter |
| **Retrieval (lange Kontexte)** | gut | besser |
| **Mehrsprachig** | Nein (English-only) | Nein (English-only) |
| **Lizenz** | Apache 2.0 | Apache 2.0 |
| **RAM-Bedarf** | ~0.5 GB | ~1.3 GB |

Quellen: [MTEB Benchmark](https://huggingface.co/spaces/mteb/leaderboard),
[RAG comparison](https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag)

## Warum nomic-embed-text für dieses Projekt

**Entscheidend: Context Window**

ETSI-Normtext-Segmente aus `pdf-segment.py` können 400–800 Tokens lang sein.
`mxbai-embed-large` hat ein hartes Limit von **512 Tokens** — längere Segmente
werden stillschweigend abgeschnitten, was die Embedding-Qualität zerstört.
`nomic-embed-text` verarbeitet bis zu **8 192 Tokens** ohne Verlust.

**Weitere Gründe:**

- Kleiner (274 MB vs. 670 MB) → schnelleres Laden in LM Studio
- Flexible Dimensionen (MRL): Index kann auf 256 Dims komprimiert werden
  wenn Speicher knapp wird — ohne Neu-Embedding
- Auf kurzen Retrieval-Queries (typische MCP-Anfragen) performt nomic
  besser als mxbai
- Beide Modelle sind English-only; da ETSI-Specs auf Englisch sind,
  kein Nachteil

## Upgrade-Pfad

Falls später bessere Qualität nötig:

| Option | MTEB | Context | Größe | Bemerkung |
|---|---|---|---|---|
| `nomic-embed-text-v1.5` | 62.3 | 8 192 | 274 MB | **default** |
| `jina-embeddings-v3` | ~68 | 8 192 | 570 MB | Apache 2.0, multilingual |
| `bge-m3` | 63 | 8 192 | 1.2 GB | MIT, stärkste Retrieval-Accuracy |
| `mxbai-embed-large` | 64 | **512** | 670 MB | ❌ zu kurzes Context-Window |

Die Index-Schema in `build-index.py` ist modellunabhängig — Dimensionen
werden beim Build aus dem Modell-Response gelesen. Embedding-Modell
wechseln = `build-index.py --rebuild` reicht.

## Konfiguration

In `.env` (bzw. `.env.example`):

```env
# Embedding model für MCP-Index (via LM Studio /v1/embeddings)
EMBEDDING_MODEL=nomic-embed-text-v1.5
EMBEDDING_DIMENSIONS=768
# Reduktion auf 256 möglich: EMBEDDING_DIMENSIONS=256
```

In LM Studio: Modell laden unter **"Embedding"** Tab, nicht unter "Chat".
