import json
import pytest

import meeting_type_config as mtc


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear module-level cache between tests."""
    mtc._meeting_types = None
    yield
    mtc._meeting_types = None


@pytest.fixture(autouse=True)
def patch_mtc_file(meeting_types_file, monkeypatch):
    monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", meeting_types_file)


class TestFindDistroList:
    def test_exact_substring_match(self):
        result = mtc.find_distro_list("engineering standup")
        assert result == ["engineering-team@company.com"]

    def test_case_insensitive_match(self):
        result = mtc.find_distro_list("Weekly Engineering Standup")
        assert result == ["engineering-team@company.com"]

    def test_case_insensitive_upper(self):
        result = mtc.find_distro_list("PRODUCT SYNC MEETING")
        assert result == ["product-team@company.com", "pm-leads@company.com"]

    def test_no_match_returns_empty_list(self):
        result = mtc.find_distro_list("Board of Directors Quarterly Review")
        assert result == []

    def test_first_match_wins(self, tmp_path, monkeypatch):
        # Two keys that both match the title -- first in file order wins
        data = {
            "alpha": ["alpha@company.com"],
            "alpha beta": ["alphabeta@company.com"],
        }
        path = tmp_path / "mt.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", str(path))
        mtc._meeting_types = None

        result = mtc.find_distro_list("alpha beta meeting")
        assert result == ["alpha@company.com"]

    def test_missing_file_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", "/nonexistent/path.json")
        mtc._meeting_types = None
        result = mtc.find_distro_list("anything")
        assert result == []

    def test_malformed_json_returns_empty_list_and_logs_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{NOT VALID JSON", encoding="utf-8")
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", str(bad_file))
        mtc._meeting_types = None

        import logging
        with caplog.at_level(logging.WARNING):
            result = mtc.find_distro_list("engineering standup")

        assert result == []
        assert any("parse" in r.message.lower() or "failed" in r.message.lower() for r in caplog.records)

    def test_non_object_json_returns_empty_list(self, tmp_path, monkeypatch):
        list_file = tmp_path / "list.json"
        list_file.write_text('["a", "b"]', encoding="utf-8")
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", str(list_file))
        mtc._meeting_types = None

        result = mtc.find_distro_list("anything")
        assert result == []
