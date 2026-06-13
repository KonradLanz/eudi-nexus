#!/usr/bin/env python3
"""
build-index-alias.py — compatibility shim

The canonical build-index script uses a hyphen in its filename:

    python3 scripts/build-index.py

But callers (Makefile snippets, old shell history, docs) sometimes use
an underscore:

    python3 scripts/build_index.py   ← Errno 2: No such file

This shim re-executes the real script so both spellings work.
It lives at scripts/build-index-alias.py and can be symlinked if desired:

    ln -s build-index.py scripts/build_index.py

But since git doesn't track symlinks reliably on Windows, this shim
exists as a fallback for contributors who can't create symlinks.
"""
import runpy
import sys
from pathlib import Path

_REAL = Path(__file__).parent / "build-index.py"

if not _REAL.is_file():
    sys.exit(f"[ERROR] Cannot find {_REAL} — check your working directory.")

# Re-execute the real script in the same interpreter process.
# sys.argv[0] is patched so the real script sees the right name.
sys.argv[0] = str(_REAL)
runpy.run_path(str(_REAL), run_name="__main__")
