import json
import os
import pytest


BILL_EMAIL = "bill.johnson@scribendi.com"

SAMPLE_MEETING_TYPES = {
    "engineering standup": ["engineering-team@company.com"],
    "product sync": ["product-team@company.com", "pm-leads@company.com"],
    "client review": ["client-success@company.com"],
}


@pytest.fixture
def meeting_types_file(tmp_path):
    """Write a sample meeting_types.json and return its path."""
    path = tmp_path / "meeting_types.json"
    path.write_text(json.dumps(SAMPLE_MEETING_TYPES), encoding="utf-8")
    return str(path)


@pytest.fixture
def empty_meeting_types_file(tmp_path):
    path = tmp_path / "meeting_types.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def storage_file(tmp_path):
    """Return path to a temporary processed_meetings.json (does not exist yet)."""
    return str(tmp_path / "processed_meetings.json")


@pytest.fixture(autouse=True)
def patch_bill_email(monkeypatch):
    """Ensure config.EMAIL_CC is always the test value."""
    import config
    monkeypatch.setattr(config, "EMAIL_CC", BILL_EMAIL)
