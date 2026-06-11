# ETSI Word Style Map

Reference for all paragraph style names observed across the 15 ETSI DOCX files
in `downloads/specs/EN/`. Used by `scripts/pdf-segment.py` to classify each
paragraph into a segment type without heuristics.

Derived from a corpus scan (June 2026). Re-run the scan after adding new specs:

```bash
python3 - <<'EOF'
import docx, collections, pathlib
total: collections.Counter = collections.Counter()
doc_count: collections.Counter = collections.Counter()
for path in sorted(pathlib.Path("downloads/specs").rglob("*.docx")):
    if path.name.startswith("."):
        continue
    try:
        d = docx.Document(str(path))
        seen = set()
        for p in d.paragraphs:
            if p.text.strip():
                s = p.style.name or ""
                total[s] += 1
                seen.add(s)
        for s in seen:
            doc_count[s] += 1
    except Exception as e:
        print(f"✗ {path.name}: {e}")
print(f"{'Style':<25} {'Total':>7}  {'Docs':>5}")
print("─" * 42)
for s, n in total.most_common():
    if doc_count[s] >= 2:
        print(f"  {s:<25} {n:>7,}  {doc_count[s]:>4}x")
EOF
```

---

## Style → Segment Type Reference

### Headings & Structure → `SECTION`

| Style | Docs | Notes |
|---|---|---|
| `Heading 1` | 15/15 | Numbered top-level clause (e.g. `4 General`) |
| `Heading 2` | 15/15 | Second-level clause (e.g. `4.1 Scope`) |
| `Heading 3` | 11/15 | Third-level clause |
| `Heading 4` | 5/15 | Fourth-level clause |
| `Heading 5` | 2/15 | Fifth-level clause |
| `Heading 8` | 11/15 | Used for Annex sub-clauses (ETSI convention) |
| `Heading 9` | 2/15 | Deep Annex sub-clause |
| `H6` | 13/15 | **ETSI-specific Annex heading style** — not a Word `Heading N`; present in 13 of 15 docs; maps to `SECTION` |

> **Note:** `Heading 6` and `Heading 7` are **not used** in this corpus.
> `H6` (no space) is the ETSI-internal alternative.

---

### Cover Page → `OTHER`

| Style | Docs | Notes |
|---|---|---|
| `ZA` | 14/15 | Document title line on cover page |
| `ZB` | 13/15 | Document type ("European Standard", "Technical Specification") |
| `ZT` | 14/15 | Scope / status line on cover page |
| `ZD` | 6/15 | Additional cover metadata (version, dates) |
| `FP` | 14/15 | Front-page metadata block (Reference, Keywords, etc.) |

---

### Informative → `INFORM`

| Style | Docs | Notes |
|---|---|---|
| `NO` | 14/15 | `NOTE:` paragraphs — informative, not testable |
| `EX` | 14/15 | `EXAMPLE:` paragraphs |
| `EW` | 9/15 | Definitions / editorial notes / warnings |
| `Editor's Note` | 2/15 | Draft-stage annotations; removed before publication |

---

### Normative Body → `NORM`

All body text styles that carry RFC-2119 requirements (shall / shall not /
must / should / may).

| Style | Docs | Notes |
|---|---|---|
| `B1` | 8/15 | Requirement item, depth 1 |
| `B1+` | 12/15 | Continuation / sub-item, depth 1 (most common normative style) |
| `B2` | 8/15 | Requirement item, depth 2 |
| `B2+` | 6/15 | Continuation, depth 2 |
| `B3` | 4/15 | Requirement item, depth 3 |
| `B3+` | 3/15 | Continuation, depth 3 |
| `B4` | 4/15 | Requirement item, depth 4 |
| `BL` | 8/15 | Normative body (block / list), unnumbered |
| `BN` | 6/15 | Body numbered — numbered requirement list |
| `PL` | 3/15 | Paragraph list — non-numbered list item |
| `List Paragraph` | 5/15 | Word built-in list paragraph (auto-indented lists) |
| `Normal` | 14/15 | Plain body text — classified by RFC-2119 keyword heuristic |

> **Depth hierarchy:** `B1` > `B2` > `B3` > `B4` mirror clause nesting.
> The `+` suffix means the paragraph continues the previous item (no new
> requirement number).

---

### Tables & Figures → `TABLE`

| Style | Docs | Notes |
|---|---|---|
| `TH` | 5/15 | Table header row |
| `TF` | 4/15 | Table footer row / figure caption |
| `TT` | 14/15 | Monospace / code (treated as known, classified by content heuristic) |

> `TT` paragraphs are not mapped directly to `TABLE` — they often appear
> inside `NORM` or `INFORM` blocks and are classified by context.

---

### Table of Contents → `TOC`

| Style | Docs | Notes |
|---|---|---|
| `toc 1` | 14/15 | Top-level TOC entry |
| `toc 2` | 14/15 | Second-level TOC entry |
| `toc 3` | 10/15 | Third-level TOC entry |
| `toc 4` | 5/15 | Fourth-level TOC entry |
| `toc 5` | 2/15 | Fifth-level TOC entry |
| `toc 8` | 11/15 | TOC entry for Annex sub-clauses (mirrors `Heading 8`) |
| `toc 9` | 2/15 | TOC entry for deep Annex sub-clauses |

---

## Frequency Overview (≥2 docs, sorted by total occurrences)

| Style | Total | Docs | Mapping |
|---|---:|---:|---|
| `Normal` | 2,690 | 14 | `NORM` (by heuristic) |
| `B1+` | 730 | 12 | `NORM` |
| `NO` | 692 | 14 | `INFORM` |
| `PL` | 460 | 3 | `NORM` |
| `BL` | 410 | 8 | `NORM` |
| `EX` | 375 | 14 | `INFORM` |
| `Heading 2` | 282 | 15 | `SECTION` |
| `toc 2` | 276 | 14 | `TOC` |
| `FP` | 274 | 14 | `OTHER` |
| `B2` | 251 | 8 | `NORM` |
| `List Paragraph` | 246 | 5 | `NORM` |
| `Heading 3` | 232 | 11 | `SECTION` |
| `toc 3` | 231 | 10 | `TOC` |
| `BN` | 228 | 6 | `NORM` |
| `Heading 1` | 191 | 15 | `SECTION` |
| `toc 1` | 181 | 14 | `TOC` |
| `B2+` | 176 | 6 | `NORM` |
| `B1` | 140 | 8 | `NORM` |
| `EW` | 138 | 9 | `INFORM` |
| `B3` | 126 | 4 | `NORM` |
| `toc 4` | 120 | 5 | `TOC` |
| `Heading 4` | 120 | 5 | `SECTION` |
| `B3+` | 48 | 3 | `NORM` |
| `ZT` | 47 | 14 | `OTHER` |
| `toc 8` | 41 | 11 | `TOC` |
| `Heading 8` | 40 | 11 | `SECTION` |
| `TH` | 38 | 5 | `TABLE` |
| `B4` | 35 | 4 | `NORM` |
| `H6` | 33 | 13 | `SECTION` |
| `toc 5` | 32 | 2 | `TOC` |
| `Heading 5` | 32 | 2 | `SECTION` |
| `TF` | 23 | 4 | `TABLE` |
| `ZA` | 14 | 14 | `OTHER` |
| `TT` | 14 | 14 | *(known, context-classified)* |
| `ZB` | 13 | 13 | `OTHER` |
| `Editor's Note` | 7 | 2 | `INFORM` |
| `ZD` | 6 | 6 | `OTHER` |
| `toc 9` | 5 | 2 | `TOC` |
| `Heading 9` | 4 | 2 | `SECTION` |

---

## Adding New Styles

When `--scan-styles` reports a `← UNKNOWN` style:

1. Note the style name and a sample paragraph from the output.
2. Determine the correct segment type:
   - Is it a heading/clause title? → `SECTION`
   - Does it carry `shall`/`must`/`should`? → `NORM`
   - Is it a `NOTE:` or `EXAMPLE:`? → `INFORM`
   - Is it a table row? → `TABLE`
   - Is it cover-page metadata? → `OTHER`
3. Add the lowercase style name to `_ETSI_STYLE_MAP` in `scripts/pdf-segment.py`.
4. Add the prefix/name to `_KNOWN_STYLE_PREFIXES` so the warning is suppressed.
5. Update the tables in this file.

---

## AI Fallback (Planned)

For documents that use non-standard Word templates (contributions, liaison
statements, ad-hoc papers), the static map may be insufficient. The planned
fallback uses a local model (Ollama / llama.cpp) with the following prompt:

```
You are classifying paragraphs from an ETSI standards document.
Given a Word paragraph style named "{style}" with the following sample text:

  "{sample_text}"

Classify it as exactly one of:
  SECTION  — a heading or clause title
  NORM     — normative body text (contains shall/must/should or requirements)
  INFORM   — informative text (NOTE:, EXAMPLE:, definitions, editorial notes)
  TABLE    — a table row, table caption, or figure caption
  TOC      — a table-of-contents line
  OTHER    — cover page, boilerplate, metadata, or blank

Respond with only the classification word.
```

This prompt is intentionally minimal so it works with small models (≥7B).
The result is cached per `(style_name, first_50_chars_of_sample)` to avoid
redundant inference calls.

**Trigger condition:** `_warn_unknown_styles()` in `pdf-segment.py` emits a
`TODO` comment and the style name. The AI fallback will be invoked there when
Ollama is detected on the local machine.

**Copyright note:** Local AI inference over standards documents constitutes
fair use under EU and international copyright law (Art. 5(3) InfoSoc
Directive). No document content is sent to external services.
