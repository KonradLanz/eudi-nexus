"""
Debug script: compare pdfplumber vs pdftotext for doubled-char artifacts in tr_103684.
Run: python3 scripts/debug_doubled.py
"""
import re
import subprocess
import sys
from pathlib import Path

PDF = Path("downloads/specs/TR/tr_103684v010101p.pdf")

# --- pdfplumber extraction ---
try:
    import pdfplumber
    with pdfplumber.open(PDF) as pdf:
        plumber_text = "".join(p.extract_text() or "" for p in pdf.pages[:20])
    print(f"pdfplumber: extracted {len(plumber_text)} chars from first 20 pages")
except ImportError:
    print("pdfplumber not available")
    plumber_text = ""

# --- pdftotext extraction ---
result = subprocess.run(
    ["pdftotext", "-f", "1", "-l", "20", str(PDF), "-"],
    capture_output=True, text=True
)
poppler_text = result.stdout
print(f"pdftotext:  extracted {len(poppler_text)} chars from first 20 pages")

# --- Pattern: sequence of doubled chars separated by spaces
# e.g. "TT rr uu ss tt" would match as doubled chars
# We look for 3+ consecutive tokens where each token is a doubled letter pair
doubled_pat = re.compile(
    r"\b([A-Za-z])\1 ([A-Za-z])\2 ([A-Za-z])\3\b"
)

plumber_matches = doubled_pat.findall(plumber_text)
poppler_matches = doubled_pat.findall(poppler_text)

print(f"\n=== Triple-doubled pattern (e.g. 'TT rr uu ss') ===")
print(f"  pdfplumber: {len(plumber_matches)} matches")
print(f"  pdftotext:  {len(poppler_matches)} matches")

# Show contexts from pdfplumber
print("\n--- pdfplumber contexts ---")
for m in doubled_pat.finditer(plumber_text):
    s = m.start()
    ctx = plumber_text[max(0, s-60):s+80].replace("\n", " ")
    print(f"  MATCH: {repr(m.group())}")
    print(f"  CTX:   {repr(ctx)}")
    print()
    if plumber_matches.index(m.groups()) >= 4:
        break

# --- Also check for any word where EVERY char is doubled
# pattern: word-like token where chars come in pairs
word_doubled = re.compile(r"\b(([A-Za-z])\2 ){3,}([A-Za-z])\3\b")
wm_plumber = word_doubled.findall(plumber_text)
wm_poppler = word_doubled.findall(poppler_text)
print(f"\n=== Word-level doubled (4+ doubled pairs) ===")
print(f"  pdfplumber: {len(wm_plumber)}")
print(f"  pdftotext:  {len(wm_poppler)}")

for m in word_doubled.finditer(plumber_text):
    s = m.start()
    ctx = plumber_text[max(0, s-60):s+80].replace("\n", " ")
    print(f"  WORD DOUBLED: {repr(m.group())}")
    print(f"  CTX: {repr(ctx)}")
    print()

# --- Also: save excerpt for manual comparison ---
with open("/tmp/plumber_excerpt.txt", "w") as f:
    # Find a page with figure/diagram content (likely where artifacts occur)
    f.write(plumber_text[:8000])

with open("/tmp/poppler_excerpt.txt", "w") as f:
    f.write(poppler_text[:8000])

print("\nSaved excerpts to /tmp/plumber_excerpt.txt and /tmp/poppler_excerpt.txt")
print("Run: diff /tmp/plumber_excerpt.txt /tmp/poppler_excerpt.txt | head -60")
