import json
import os
import threading

import pytest

import storage as st


@pytest.fixture(autouse=True)
def patch_storage_file(storage_file, monkeypatch):
    monkeypatch.setattr(st, "STORAGE_FILE", storage_file)
    monkeypatch.setattr(st, "_LOCK_FILE", storage_file + ".lock")


class TestIsProcessed:
    def test_returns_false_for_new_id(self):
        assert st.is_processed("meeting-001") is False

    def test_returns_true_after_mark_processed(self):
        st.mark_processed("meeting-001", "Standup")
        assert st.is_processed("meeting-001") is True

    def test_returns_false_for_different_id(self):
        st.mark_processed("meeting-001", "Standup")
        assert st.is_processed("meeting-002") is False


class TestMarkProcessed:
    def test_creates_file_if_missing(self, storage_file):
        assert not os.path.exists(storage_file)
        st.mark_processed("meeting-001", "Standup")
        assert os.path.exists(storage_file)

    def test_stores_title_and_processed_at(self, storage_file):
        st.mark_processed("meeting-001", "Sprint Review")
        with open(storage_file, encoding="utf-8") as f:
            data = json.load(f)
        assert "meeting-001" in data
        entry = data["meeting-001"]
        assert entry["title"] == "Sprint Review"
        assert "processed_at" in entry

    def test_multiple_meetings_stored(self, storage_file):
        st.mark_processed("m-1", "A")
        st.mark_processed("m-2", "B")
        with open(storage_file, encoding="utf-8") as f:
            data = json.load(f)
        assert set(data.keys()) == {"m-1", "m-2"}


class TestCorruptionHandling:
    def test_corrupted_file_triggers_backup_and_fresh_start(self, storage_file):
        with open(storage_file, "w", encoding="utf-8") as f:
            f.write("NOT VALID JSON {{{")

        result = st.is_processed("meeting-001")
        assert result is False

        backup_files = [
            f
            for f in os.listdir(os.path.dirname(storage_file))
            if "corrupt" in f
        ]
        assert len(backup_files) == 1

    def test_can_write_after_corruption_recovery(self, storage_file):
        with open(storage_file, "w", encoding="utf-8") as f:
            f.write("BROKEN")

        st.mark_processed("meeting-001", "Recovery Test")
        assert st.is_processed("meeting-001") is True


class TestConcurrentAccess:
    def test_concurrent_writes_do_not_corrupt_file(self, storage_file):
        errors: list[Exception] = []

        def write_meeting(idx: int) -> None:
            try:
                st.mark_processed(f"meeting-{idx}", f"Meeting {idx}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write_meeting, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        with open(storage_file, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 10
