#!/usr/bin/env python3
"""
test/test_pdf_segment_rules.py

Unit tests for the JSON-based para-tune rule logic intended for scripts/pdf-segment.py.

These tests are written against the planned helper:
    _get_rule_for_stem(stem: str) -> dict

Expected behavior:
- Reads corpus/para-tune/learned-rules.json directly
- Returns {} if file is missing
- Returns {} if JSON is invalid
- Returns {} if stem is unknown
- Returns the matching rule dict for known stems

Run:
  pytest test/test_pdf_segment_rules.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load_pdf_segment():
    spec = importlib.util.spec_from_file_location(
        "pdf_segment", ROOT / "scripts" / "pdf-segment.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pdf_segment = _load_pdf_segment()


@pytest.fixture
def sample_rules() -> dict:
    return {
        "ts_11914402v020101p": {
            "gap_threshold": 18,
            "merge_endings": [";", ","],
            "no_merge_starts": ["NOTE"],
            "tuned_at": "2026-06-13T21:00:00Z",
            "method": "grid",
        },
        "en_319401v020201p": {
            "gap_threshold": 24,
            "merge_endings": [],
            "no_merge_starts": [],
            "tuned_at": "2026-06-13T22:00:00Z",
            "method": "ai",
        },
    }


@pytest.fixture
def rules_file(tmp_path: Path, sample_rules: dict) -> Path:
    path = tmp_path / "corpus" / "para-tune" / "learned-rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample_rules), encoding="utf-8")
    return path


class TestJsonRuleLoading:
    def test_helper_exists(self):
        assert hasattr(pdf_segment, "_get_rule_for_stem"), (
            "pdf-segment.py should expose _get_rule_for_stem(stem) for JSON-based rule loading"
        )

    def test_known_stem_returns_rule(self, monkeypatch: pytest.MonkeyPatch, rules_file: Path):
        monkeypatch.setattr(pdf_segment, "RULES_PATH", rules_file)

        rule = pdf_segment._get_rule_for_stem("ts_11914402v020101p")

        assert rule["gap_threshold"] == 18
        assert rule["merge_endings"] == [";", ","]
        assert rule["no_merge_starts"] == ["NOTE"]
        assert rule["method"] == "grid"

    def test_unknown_stem_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch, rules_file: Path):
        monkeypatch.setattr(pdf_segment, "RULES_PATH", rules_file)

        rule = pdf_segment._get_rule_for_stem("unknown_stem")

        assert rule == {}

    def test_missing_rules_file_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        missing = tmp_path / "corpus" / "para-tune" / "does-not-exist.json"
        monkeypatch.setattr(pdf_segment, "RULES_PATH", missing)

        rule = pdf_segment._get_rule_for_stem("ts_11914402v020101p")

        assert rule == {}

    def test_invalid_json_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        broken = tmp_path / "corpus" / "para-tune" / "learned-rules.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{ this is not valid json", encoding="utf-8")
        monkeypatch.setattr(pdf_segment, "RULES_PATH", broken)

        rule = pdf_segment._get_rule_for_stem("ts_11914402v020101p")

        assert rule == {}

    def test_rule_dict_is_not_modified(self, monkeypatch: pytest.MonkeyPatch, rules_file: Path):
        monkeypatch.setattr(pdf_segment, "RULES_PATH", rules_file)

        rule = pdf_segment._get_rule_for_stem("en_319401v020201p")
        rule["gap_threshold"] = 999

        fresh_rule = pdf_segment._get_rule_for_stem("en_319401v020201p")

        assert fresh_rule["gap_threshold"] == 24


class TestRuleValueConsumption:
    def test_rule_values_fall_back_to_defaults(self):
        rule = {}

        gap_threshold = int(rule.get("gap_threshold", 20))
        merge_endings = tuple(rule.get("merge_endings", []))
        no_merge_starts = tuple(rule.get("no_merge_starts", []))

        assert gap_threshold == 20
        assert merge_endings == ()
        assert no_merge_starts == ()

    def test_rule_values_are_extracted_correctly(self, sample_rules: dict):
        rule = sample_rules["ts_11914402v020101p"]

        gap_threshold = int(rule.get("gap_threshold", 20))
        merge_endings = tuple(rule.get("merge_endings", []))
        no_merge_starts = tuple(rule.get("no_merge_starts", []))

        assert gap_threshold == 18
        assert merge_endings == (";", ",")
        assert no_merge_starts == ("NOTE",)

    def test_partial_rule_still_uses_defaults(self):
        rule = {"gap_threshold": 16}

        gap_threshold = int(rule.get("gap_threshold", 20))
        merge_endings = tuple(rule.get("merge_endings", []))
        no_merge_starts = tuple(rule.get("no_merge_starts", []))

        assert gap_threshold == 16
        assert merge_endings == ()
        assert no_merge_starts == ()
