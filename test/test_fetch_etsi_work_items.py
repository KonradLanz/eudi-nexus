#!/usr/bin/env python3
"""
Tests for fetch-etsi-work-items.py

Covers:
  - Grace period logic (no network if cache < 24h)
  - ETag / 304 handling (fetched_at refreshed, HTML from cache)
  - Pagination additive logic (offset advances when items < total)
  - extract_total_from_html helper
  - count_items_in_html helper

All network calls are mocked via monkeypatch.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
import importlib

import pytest

# ---------------------------------------------------------------------------
# Import the module under test  (scripts/ is not a package, use importlib)
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# We must patch PAGES_DIR before the module caches it, so import lazily
def import_module(tmp_path: Path):
    """Import fetch_etsi_work_items with PAGES_DIR redirected to tmp_path."""
    import importlib.util, types
    spec = importlib.util.spec_from_file_location(
        "fetch_etsi_work_items",
        SCRIPTS_DIR / "fetch-etsi-work-items.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Override paths before exec
    mod.__dict__["PAGES_DIR"] = tmp_path
    mod.__dict__["DOWNLOADS_DIR"] = tmp_path.parent
    spec.loader.exec_module(mod)
    # Fix paths used inside functions
    mod.PAGES_DIR = tmp_path
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><body>
  <a href="Report_WorkItem.asp?WKI_ID=111&curItemNr=1&totalNrItems=12&optDisplay=10">
  <a href="Report_WorkItem.asp?WKI_ID=222&curItemNr=2&totalNrItems=12&optDisplay=10">
  <a href="Report_WorkItem.asp?WKI_ID=333&curItemNr=3&totalNrItems=12&optDisplay=10">
</body></html>
"""


@pytest.fixture
def mod(tmp_path):
    """Module with PAGES_DIR set to pytest tmp_path."""
    m = import_module(tmp_path)
    return m


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_extract_total(self, mod):
        assert mod.extract_total_from_html(SAMPLE_HTML) == 12

    def test_extract_total_missing(self, mod):
        assert mod.extract_total_from_html("<html>no links</html>") is None

    def test_count_items(self, mod):
        assert mod.count_items_in_html(SAMPLE_HTML) == 3

    def test_count_items_empty(self, mod):
        assert mod.count_items_in_html("<html></html>") == 0

    def test_age_seconds_recent(self, mod):
        now = datetime.now(timezone.utc).isoformat()
        age = mod.age_seconds(now)
        assert 0 <= age < 5  # should be nearly 0

    def test_age_seconds_old(self, mod):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        age = mod.age_seconds(old)
        assert age > 24 * 3600

    def test_age_seconds_invalid(self, mod):
        assert mod.age_seconds("not-a-date") == float("inf")


# ---------------------------------------------------------------------------
# Unit tests: fetch_page grace period
# ---------------------------------------------------------------------------

class TestGracePeriod:
    def test_skips_when_cache_fresh(self, mod, tmp_path):
        """fetch_page returns (None, 'grace') when cache < 24h."""
        offset = 0
        # Write a young sidecar
        meta = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "etag": '"abc123"',
            "last_modified": "Sun, 14 Jun 2026 00:00:00 GMT",
        }
        mod.meta_path(offset).write_text(json.dumps(meta), encoding="utf-8")
        mod.html_path(offset).write_text(SAMPLE_HTML, encoding="utf-8")

        # Should NOT call urlopen
        with patch("urllib.request.urlopen") as mock_open:
            html, status = mod.fetch_page(offset)

        assert status == "grace"
        assert html is None  # grace returns None; caller reads from file
        mock_open.assert_not_called()

    def test_fetches_when_cache_old(self, mod, tmp_path):
        """fetch_page calls network when cache > 24h."""
        offset = 0
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        meta = {"fetched_at": old_ts, "etag": '"stale"', "last_modified": None}
        mod.meta_path(offset).write_text(json.dumps(meta), encoding="utf-8")
        mod.html_path(offset).write_text("old content", encoding="utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = SAMPLE_HTML.encode("latin-1")
        mock_resp.headers = {
            "ETag": '"newetag"',
            "Last-Modified": "Mon, 15 Jun 2026 00:00:00 GMT",
            "Content-Length": str(len(SAMPLE_HTML)),
        }
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            html, status = mod.fetch_page(offset)

        assert status == "fresh"
        assert "WKI_ID" in html
        # Sidecar updated
        saved = json.loads(mod.meta_path(offset).read_text())
        assert saved["etag"] == '"newetag"'

    def test_304_updates_fetched_at(self, mod, tmp_path):
        """304 Not Modified: sidecar fetched_at is refreshed, returns cached HTML."""
        import urllib.error

        offset = 0
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        meta = {"fetched_at": old_ts, "etag": '"known"', "last_modified": None, "status": 200}
        mod.meta_path(offset).write_text(json.dumps(meta), encoding="utf-8")
        mod.html_path(offset).write_text(SAMPLE_HTML, encoding="utf-8")

        http_304 = urllib.error.HTTPError(
            url=mod.page_url(offset), code=304,
            msg="Not Modified", hdrs=None, fp=None
        )

        with patch("urllib.request.urlopen", side_effect=http_304):
            html, status = mod.fetch_page(offset)

        assert status == "not_modified"
        # fetched_at refreshed → within last 5s
        saved = json.loads(mod.meta_path(offset).read_text())
        age = mod.age_seconds(saved["fetched_at"])
        assert age < 5, f"fetched_at not updated: age={age}s"


# ---------------------------------------------------------------------------
# Integration test: fetch_all pagination
# ---------------------------------------------------------------------------

class TestFetchAllPagination:
    def _make_html(self, offset, total, count):
        """Generate minimal HTML with `count` WKI_ID links and totalNrItems=total."""
        links = "\n".join(
            f'<a href="Report_WorkItem.asp?WKI_ID={offset+i}&curItemNr={i}&totalNrItems={total}">'
            for i in range(count)
        )
        return f"<html><body>{links}</body></html>"

    def test_single_page_all_items(self, mod):
        """When total=5 and PAGE_SIZE=999, only one fetch."""
        html = self._make_html(0, 5, 5)

        call_count = [0]
        def fake_fetch(offset):
            call_count[0] += 1
            # Write HTML to disk so caller can read it
            mod.html_path(offset).write_text(html, encoding="utf-8")
            return html, "fresh"

        mod.fetch_page = fake_fetch
        pages = mod.fetch_all()

        assert len(pages) == 1
        assert call_count[0] == 1

    def test_additive_pagination(self, mod):
        """When PAGE_SIZE < total, fetch_all fetches additional offsets."""
        # Simulate: total=25, PAGE_SIZE set to 10 for this test
        orig_page_size = mod.PAGE_SIZE
        mod.PAGE_SIZE = 10

        pages_fetched = []

        def fake_fetch(offset):
            count = min(10, 25 - offset)
            html = self._make_html(offset, 25, count)
            mod.html_path(offset).write_text(html, encoding="utf-8")
            pages_fetched.append(offset)
            return html, "fresh"

        mod.fetch_page = fake_fetch
        # Also patch sleep so test is fast
        with patch("time.sleep"):
            pages = mod.fetch_all()

        mod.PAGE_SIZE = orig_page_size  # restore

        assert pages_fetched == [0, 10, 20], f"Expected [0, 10, 20], got {pages_fetched}"
        assert len(pages) == 3
        total_items = sum(mod.count_items_in_html(p) for p in pages)
        assert total_items == 25
