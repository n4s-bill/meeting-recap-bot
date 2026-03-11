import json
import pytest

import meeting_type_config as mtc
import recipient_resolver as rr
from tests.conftest import BILL_EMAIL


@pytest.fixture(autouse=True)
def reset_mtc_cache():
    mtc._meeting_types = None
    yield
    mtc._meeting_types = None


@pytest.fixture(autouse=True)
def patch_mtc_file(meeting_types_file, monkeypatch):
    monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", meeting_types_file)


TITLE_WITH_DISTRO = "Weekly Engineering Standup"
TITLE_NO_MATCH = "Ad Hoc Discussion"


class TestTier1PayloadParticipants:
    def test_participants_used_as_to(self):
        result = rr.resolve("Any Meeting", ["alice@co.com", "bob@co.com"])
        assert result.to == ["alice@co.com", "bob@co.com"]

    def test_bill_added_to_cc_when_not_in_to(self):
        result = rr.resolve("Any Meeting", ["alice@co.com"])
        assert BILL_EMAIL in result.cc

    def test_participants_normalized_to_lowercase(self):
        result = rr.resolve("Any Meeting", ["Alice@Co.COM"])
        assert result.to == ["alice@co.com"]

    def test_duplicate_participants_deduplicated(self):
        result = rr.resolve("Any Meeting", ["alice@co.com", "ALICE@CO.COM"])
        assert result.to == ["alice@co.com"]
        assert len(result.to) == 1


class TestTier1BillDeduplication:
    def test_bill_in_participants_not_duplicated_in_cc(self):
        result = rr.resolve("Any Meeting", [BILL_EMAIL, "alice@co.com"])
        assert BILL_EMAIL not in result.cc

    def test_bill_as_only_participant_no_cc(self):
        result = rr.resolve("Any Meeting", [BILL_EMAIL])
        assert result.to == [BILL_EMAIL]
        assert result.cc == []


class TestTier1InvalidEmails:
    def test_empty_strings_filtered_out(self):
        result = rr.resolve(TITLE_WITH_DISTRO, ["", "  "])
        assert result.to == ["engineering-team@company.com"]

    def test_invalid_email_filtered_out(self):
        result = rr.resolve(TITLE_WITH_DISTRO, ["not-an-email", "also bad"])
        assert result.to == ["engineering-team@company.com"]

    def test_mix_of_valid_and_invalid(self):
        result = rr.resolve("Any Meeting", ["alice@co.com", "bad-email"])
        assert result.to == ["alice@co.com"]

    def test_none_participants_falls_through(self):
        result = rr.resolve(TITLE_WITH_DISTRO, None)
        assert result.to == ["engineering-team@company.com"]


class TestTier2DistroList:
    def test_distro_list_used_when_no_participants(self):
        result = rr.resolve(TITLE_WITH_DISTRO, [])
        assert result.to == ["engineering-team@company.com"]

    def test_bill_cc_when_not_in_distro_list(self):
        result = rr.resolve(TITLE_WITH_DISTRO, [])
        assert BILL_EMAIL in result.cc

    def test_multi_recipient_distro_list(self):
        result = rr.resolve("Monthly Product Sync", [])
        assert result.to == ["product-team@company.com", "pm-leads@company.com"]

    def test_bill_in_distro_list_not_duplicated_in_cc(self, monkeypatch, tmp_path):
        data = {"special meeting": [BILL_EMAIL, "alice@co.com"]}
        path = tmp_path / "mt.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", str(path))
        mtc._meeting_types = None

        result = rr.resolve("Special Meeting Notes", [])
        assert BILL_EMAIL not in result.cc


class TestTier3BillFallback:
    def test_bill_as_sole_to_when_no_participants_and_no_distro(self):
        result = rr.resolve(TITLE_NO_MATCH, [])
        assert result.to == [BILL_EMAIL]

    def test_no_cc_on_bill_fallback(self):
        result = rr.resolve(TITLE_NO_MATCH, [])
        assert result.cc == []

    def test_fallback_used_when_meeting_types_file_missing(self, monkeypatch):
        monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", "/nonexistent.json")
        mtc._meeting_types = None
        result = rr.resolve("Anything", [])
        assert result.to == [BILL_EMAIL]
        assert result.cc == []
