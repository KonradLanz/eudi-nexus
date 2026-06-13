# Para-tune Integration Patch for pdf-segment.py

This document describes the exact code changes needed to integrate
`para-tune.py` learned rules into `pdf-segment.py`.

> **Status**: The patch below has been reviewed and is ready to apply.
> Apply by editing `scripts/pdf-segment.py` at the marked locations.

---

## Change 1 — Import learned-rules loader (after existing imports, ~line 55)

Add after the `from pathlib import Path` import block:

```python
# ── Para-tune: per-stem GAP and merge rules ───────────────────────────────
import json as _json
_PARA_TUNE_RULES_PATH = Path("corpus/para-tune/learned-rules.json")
_PARA_TUNE_RULES_CACHE: dict | None = None


def _load_stem_rule(stem: str) -> dict:
    """
    Return the learned rule dict for *stem* from corpus/para-tune/learned-rules.json,
    or an empty dict when no rule exists or the file is absent.
    Caches the full rules file in _PARA_TUNE_RULES_CACHE after first read.
    """
    global _PARA_TUNE_RULES_CACHE
    if _PARA_TUNE_RULES_CACHE is None:
        try:
            _PARA_TUNE_RULES_CACHE = _json.loads(
                _PARA_TUNE_RULES_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            _PARA_TUNE_RULES_CACHE = {}
    return _PARA_TUNE_RULES_CACHE.get(stem, {})
# ─────────────────────────────────────────────────────────────────────────────
```

---

## Change 2 — Accept extra_endings in `_merge_paragraph_blocks()` (~line 367)

Change the function signature from:
```python
def _merge_paragraph_blocks(
    blocks: list[TextBlock],
    seg_types: list[str],
) -> tuple[list[TextBlock], list[str]]:
```
to:
```python
def _merge_paragraph_blocks(
    blocks: list[TextBlock],
    seg_types: list[str],
    extra_endings: list[str] | None = None,
    extra_no_starts: list[str] | None = None,
) -> tuple[list[TextBlock], list[str]]:
```

And inside the function body, extend `_NEW_ITEM_RE` when `extra_no_starts` is given:

```python
    # (existing _NEW_ITEM_RE definition stays as-is)
    if extra_no_starts:
        combined = (
            _NEW_ITEM_RE.pattern.rstrip(")")
            + "|"
            + "|".join(re.escape(s) for s in extra_no_starts)
            + ")"
        )
        _NEW_ITEM_RE_LOCAL = re.compile(combined, re.IGNORECASE)
    else:
        _NEW_ITEM_RE_LOCAL = _NEW_ITEM_RE
```

And change the merge guard from:
```python
            and not _SENTENCE_END_RE.search(merged_blocks[-1].text)
            and not _NEW_ITEM_RE.match(blk.text)
```
to:
```python
            and not _SENTENCE_END_RE.search(merged_blocks[-1].text)
            and (
                extra_endings is None
                or not any(merged_blocks[-1].text.rstrip().endswith(e) for e in extra_endings)
            )
            and not _NEW_ITEM_RE_LOCAL.match(blk.text)
```

---

## Change 3 — Use rule in `segment_pdf()` (~line 480)

At the top of `segment_pdf()`, after `stem = doc_stem or _safe_stem(pdf_path.stem)`, add:

```python
    # Load per-stem tuning rule (written by para-tune.py)
    _rule          = _load_stem_rule(stem)
    _gap           = _rule.get("gap_threshold", _GAP_THRESHOLD)
    _extra_endings = _rule.get("merge_endings", [])
    _extra_nostrt  = _rule.get("no_merge_starts", [])
    if _rule:
        import sys as _sys
        print(
            f"   [para-tune]  {stem}: gap={_gap}  "
            f"merge_endings={_extra_endings}  (method={_rule.get('method','?')})",
            file=_sys.stderr,
        )
```

Then patch `_extract_blocks()` call: replace the global `_GAP_THRESHOLD` constant
with the local `_gap` variable.  In the `_extract_blocks()` function, change its
signature to accept a `gap` parameter:

```python
def _extract_blocks(page, gap: int = _GAP_THRESHOLD) -> list[TextBlock]:
    ...
    if current_ys and (y - current_ys[-1]) > gap:   # ← was _GAP_THRESHOLD
```

And in `segment_pdf()`, call it as:
```python
            raw_blocks = _extract_blocks(page, gap=_gap)
```

Finally, pass extra rules into the merge step:
```python
            blocks, classified = _merge_paragraph_blocks(
                blocks, classified,
                extra_endings=_extra_endings or None,
                extra_no_starts=_extra_nostrt or None,
            )
```

---

## Validation

After applying this patch, run:

```bash
# 1. Calibrate (first run — no learned rules yet, uses defaults)
python3 scripts/para-tune.py --stats

# 2. Run full calibration
python3 scripts/para-tune.py

# 3. Verify learned rules were written
cat corpus/para-tune/learned-rules.json | python3 -m json.tool | head -30

# 4. Re-segment with learned rules
python3 scripts/para-tune.py --apply

# 5. Check improvement
python3 scripts/para-tune.py --stats
```

Expected improvement targets:
- PDF  median: 100 → ≥150 chars
- DOCX median: 113 → ≥150 chars
- PDF  `<100chars` fraction: 49% → ≤25%
- DOCX `<100chars` fraction: 44% → ≤25%
