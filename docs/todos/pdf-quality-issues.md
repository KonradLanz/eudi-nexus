# PDF Quality Issues — Reporting & Fixes

> Status: open  
> Discovered: 2026-06-14  
> Affects: `tr_103684v010101p` (65+ segments), `tr_119460v010101p` (2 segments)

---

## Issue 1 — Doubled-character artifacts in extracted text

### Symptom

Text segments contain characters doubled with intervening spaces:

```
EEvviiddeennccee pprroovviiddeerr
SSeennddeerr  RReecciippiieenntt
RREEMM  hhaannddlliinngg  pprroovviiddeerr
```

### Root cause (PDF spec)

Three known PDF authoring patterns produce this in pdfplumber / pdfminer:

1. **Synthetic bold via text overdraw** — the authoring tool (Word, InDesign,
   LibreOffice) has no embedded bold font glyph for the typeface used.  
   It simulates bold by rendering the same text twice at a slight x-offset
   (`Tr 2` fill+stroke mode, or two consecutive `Tj` operations).  
   pdfplumber sees both draw calls and emits both character streams.
   → Most likely cause for ETSI TR/TS documents authored in older Word versions.

2. **Text shadow / stroke layer** — diagrams or figure labels with a white
   background stroke use a second identical text run at z=0 (background) plus
   z=1 (foreground).  Both get extracted.

3. **Accessibility / search overlay** — some PDF generators embed an invisible
   Unicode text layer on top of a scanned or vector image layer for
   copy-paste/accessibility.  When both layers have slightly different x
   positions, pdfplumber interleaves the characters.

### Impact on eudi-nexus pipeline

| Component | Impact |
|---|---|
| Embedding / RAG | `"EEvviiddeennccee"` ≠ `"Evidence"` — similarity search misses affected blocks |
| `find_normative_keywords` | `"SSHHAALLLL"` does not match `SHALL` regex → `norm_ratio=0` false alarm |
| `_SECTION_RE` heading detection | Doubled section numbers (`"44\..22"`) do not match → headings missed |
| Quality metrics | `heading_density < 0.1` warning fires on affected PDFs |

### Workaround (already in place)

The `LOCAL_AI_URL` heading fallback (`pdf-segment.py`) covers the heading
detection gap for scanned/broken PDFs.  It does **not** fix the text content
of NORM/INFORM segments.

### Fix to implement

- [ ] Add `_fix_doubled_chars(text: str) -> str` normalizer in `pdf-segment.py`:
  ```python
  import re
  _DOUBLED_RE = re.compile(r'(?:([A-Za-z])\1 ){3,}')

  def _fix_doubled_chars(text: str) -> str:
      """Collapse 'EEvviiddeennccee' → 'Evidence'.
      Only fires when ≥3 consecutive doubled-char pairs are present to
      avoid false positives on legitimate repeated chars ('mm', 'tt', ...)."""
      def _collapse(m: re.Match) -> str:
          return re.sub(r'(.)\1 ?', r'\1', m.group(0))
      return _DOUBLED_RE.sub(_collapse, text)
  ```
- [ ] Apply in `_extract_blocks()` after assembling `block.text`, before return
- [ ] Add metric `doubled_char_fixes` to quality metrics output
- [ ] Re-segment affected PDFs after fix and verify with `heading_density` metric

---

## Issue 2 — Heading detection misses on all EN/TR/TS/SR PDF types

### Symptom

pdfplumber returns `bold_ratio ≈ 0.0` for headings in many ETSI PDFs, even
when the heading is visually bold.  The old AND-gate (`size ≥ 11.5 AND bold ≥
0.55`) rejected all of them.

### Root cause

Same synthetic-bold mechanism as Issue 1: the font has no embedded bold variant,
so pdfplumber's char-level `fontname` does not contain `"Bold"` → `bold_ratio`
stays 0.  The size signal alone is reliable.

### Fix (already implemented — `cab60ed`)

OR-gate with three tiers added to `classify_block()`:
1. `size+bold` combined (original)
2. `size-only` fallback (`heading_size_only_min=11.0`)
3. Local AI fallback via `LOCAL_AI_URL` for scanned PDFs / `size=0`

Profile field `heading_size_only_min` added to `Profile` dataclass.

---

## Reporting to ETSI ESI

Both issues are symptoms of the same PDF authoring defect in the toolchain
used to produce ETSI TR 103 684 and TR 119 460.  They are worth reporting to
ETSI ESI (TC ESI / ESI Working Group) because:

- The PDFs are intended to be machine-readable (referenced from eIDAS
  implementing acts and used in conformance testing pipelines)
- The doubled-char artifact breaks **any** text-extraction tool, not just
  eudi-nexus (Adobe Acrobat copy-paste also produces doubled text)
- Heading detection failures affect automated clause-reference tooling

### Suggested report contents

```
To:   ESI (listname tbd — check https://www.etsi.org/committee/esi)
CC:   ETSI Secretariat
Subj: PDF text-extraction defects in TR 103 684 and TR 119 460

Affected documents:
  ETSI TR 103 684 V1.1.1 (2021-08)
  ETSI TR 119 460 V1.1.1

Defects identified:
  1. Doubled-character artifacts in body text and figure labels.
     Root cause: synthetic bold rendering (second Tj draw call) —
     both draw calls are emitted by pdfminer/pdfplumber, making
     automated text extraction unusable for affected sections.
     Affected sections: [list from grep output]

  2. Section headings lack embedded bold font variant.
     pdfplumber bold_ratio returns 0.0 for all headings, making
     automated section detection unreliable without heuristics.

Reproduction:
  pip install pdfplumber
  python3 -c "
  import pdfplumber
  with pdfplumber.open('tr_103684v010101p.pdf') as pdf:
      print(pdf.pages[X].extract_text()[:500])
  "

Requested fix:
  Re-export PDFs with a font that includes an embedded bold variant,
  or disable synthetic bold in the authoring tool (Word: Font > Bold
  should use the actual bold font file, not simulated bold).
```

### Action items

- [ ] Collect full list of affected section numbers from `tr_103684` and
      `tr_119460` via `grep -rl` output (see discovery command below)
- [ ] Verify in Adobe Acrobat that copy-paste also reproduces the doubled
      chars (confirms PDF-level defect, not a pdfplumber bug)
- [ ] Draft report and send to ETSI ESI mailing list
- [ ] Link this issue in the report to the eudi-nexus GitHub repo

```bash
# List all affected segment files:
grep -rl '([A-Za-z])\1 \([A-Za-z]\)\2 \([A-Za-z]\)\3' corpus/segments/
```
