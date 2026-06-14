#!/usr/bin/env python3
"""
fetch-etsi-work-items.py

Fetches all ESI work items from the ETSI Work Programme portal.

Caching strategy:
  - Sidecar file: downloads/_pages/<slug>.meta.json  (ETag, Last-Modified, fetched_at, url)
  - 24h grace period: if sidecar exists and fetched_at < 24h ago → skip fetch entirely
  - ETag / If-None-Match: if sidecar exists but >24h → send ETag, accept 304 Not Modified
  - qNB_TO_DISPLAY=999: fetch all items in one request (581 currently)
  - Falls back to pagination (qOFFSET addiert) if server limits page size
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = (
    "https://portal.etsi.org/webapp/WorkProgram/Frame_WorkItemList.asp"
    "?qSORT=HIGHVERSION"
    "&qETSI_ALL="
    "&SearchPage=TRUE"
    "&qTB_ID=607%3BESI"
    "&qINCLUDE_SUB_TB=True"
    "&qINCLUDE_MOVED_ON="
    "&qSTOP_FLG="
    "&qKEYWORD_BOOLEAN="
    "&qCLUSTER_BOOLEAN="
    "&qFREQUENCIES_BOOLEAN="
    "&qSTOPPING_OUTDATED="
    "&butSimple=Search"
    "&includeNonActiveTB=FALSE"
    "&includeSubProjectCode="
    "&qREPORT_TYPE=SUMMARY"
)

PAGE_SIZE       = 999          # request all at once
GRACE_SECONDS   = 24 * 3600   # 24h grace period
USER_AGENT      = "eudi-nexus/1.0 (standards research; contact: koni@example.com)"

DOWNLOADS_DIR   = Path(__file__).parent.parent / "downloads"
PAGES_DIR       = DOWNLOADS_DIR / "_pages"

PAGES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def page_url(offset: int, nb: int = PAGE_SIZE) -> str:
    return f"{BASE_URL}&qOFFSET={offset}&qNB_TO_DISPLAY={nb}"


def slug(offset: int) -> str:
    return f"etsi_esi_work_items_offset{offset}"


def html_path(offset: int) -> Path:
    return PAGES_DIR / f"{slug(offset)}.html"


def meta_path(offset: int) -> Path:
    return PAGES_DIR / f"{slug(offset)}.meta.json"


def load_meta(offset: int) -> dict:
    p = meta_path(offset)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_meta(offset: int, meta: dict) -> None:
    meta_path(offset).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_seconds(fetched_at: str) -> float:
    """Seconds since fetched_at ISO timestamp."""
    try:
        dt = datetime.fromisoformat(fetched_at)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


def count_items_in_html(html: str) -> int:
    """Count work item rows by looking for WKI_ID links."""
    return html.count("WKI_ID=")


def extract_total_from_html(html: str) -> int | None:
    """Extract totalNrItems=NNN from any detail link in the HTML."""
    import re
    m = re.search(r"totalNrItems=(\d+)", html)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Fetch one page with ETag / 304 support
# ---------------------------------------------------------------------------

def fetch_page(offset: int) -> tuple[str | None, str]:
    """
    Returns (html_content_or_None, status) where status is one of:
      'fresh'      – fetched new content (200)
      'not_modified' – server returned 304, cached file still valid
      'grace'      – within 24h grace, skipped network request entirely
      'error'      – HTTP or network error
    """
    url  = page_url(offset)
    meta = load_meta(offset)
    hp   = html_path(offset)

    # --- Grace period: skip if cached & young enough ---------------------
    if meta.get("fetched_at") and hp.exists():
        age = age_seconds(meta["fetched_at"])
        if age < GRACE_SECONDS:
            remaining = int((GRACE_SECONDS - age) / 3600)
            print(f"  [GRACE] offset={offset}: cache is {int(age/3600)}h old, skipping "
                  f"(expires in ~{remaining}h)")
            return None, "grace"

    # --- Build request with conditional headers --------------------------
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    if meta.get("etag"):
        req.add_header("If-None-Match", meta["etag"])
    if meta.get("last_modified"):
        req.add_header("If-Modified-Since", meta["last_modified"])

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("latin-1")  # ETSI portal uses latin-1

            new_meta = {
                "url":           url,
                "offset":        offset,
                "fetched_at":    now_iso(),
                "etag":          resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "content_length":resp.headers.get("Content-Length"),
                "status":        resp.status,
            }
            save_meta(offset, new_meta)
            hp.write_text(html, encoding="utf-8")

            print(f"  [200 OK] offset={offset}: {len(html):,} chars, "
                  f"ETag={new_meta['etag']}, Last-Modified={new_meta['last_modified']}")
            return html, "fresh"

    except urllib.error.HTTPError as e:
        if e.code == 304:
            # Not Modified — update only fetched_at so grace period resets
            meta["fetched_at"] = now_iso()
            meta["status"]     = 304
            save_meta(offset, meta)
            print(f"  [304 Not Modified] offset={offset}: cache still valid")
            # Return cached HTML
            return hp.read_text(encoding="utf-8") if hp.exists() else None, "not_modified"
        else:
            print(f"  [HTTP ERROR {e.code}] offset={offset}: {e.reason}")
            return None, "error"

    except Exception as ex:
        print(f"  [ERROR] offset={offset}: {ex}")
        return None, "error"


# ---------------------------------------------------------------------------
# Main: fetch all pages (additive pagination if needed)
# ---------------------------------------------------------------------------

def fetch_all() -> list[str]:
    """
    Fetches all pages additively.
    First tries offset=0 with qNB_TO_DISPLAY=999.
    If the server returns fewer items than expected, paginates in steps.
    Returns list of HTML strings (from cache or fresh).
    """
    all_html: list[str] = []
    offset = 0

    print(f"\n=== ETSI ESI Work Items Fetch ===")
    print(f"  Grace period : {GRACE_SECONDS // 3600}h")
    print(f"  Page size    : {PAGE_SIZE}")
    print(f"  Pages dir    : {PAGES_DIR}")
    print()

    while True:
        html, status = fetch_page(offset)

        # Use cached file if grace/304
        if status in ("grace", "not_modified"):
            hp = html_path(offset)
            html = hp.read_text(encoding="utf-8") if hp.exists() else None

        if not html:
            if status == "error":
                print(f"  Stopping at offset={offset} due to error.")
            break

        all_html.append(html)

        # Check if we got everything
        total = extract_total_from_html(html)
        items_in_page = count_items_in_html(html)
        print(f"  → offset={offset}: {items_in_page} items in page, "
              f"total reported={total}")

        next_offset = offset + PAGE_SIZE
        if total is None or next_offset >= total:
            print(f"  All items fetched (offset {offset} + {PAGE_SIZE} >= {total}).")
            break

        # More pages needed
        offset = next_offset
        time.sleep(1.5)  # polite delay between requests

    return all_html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pages = fetch_all()
    total_items = sum(count_items_in_html(p) for p in pages)
    print(f"\nDone. {len(pages)} page(s), {total_items} item links total.")
    print("HTML files saved to:", PAGES_DIR)
