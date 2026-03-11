import json
import logging
import os
import shutil
from datetime import datetime, timezone

from filelock import FileLock

logger = logging.getLogger(__name__)

STORAGE_FILE = os.environ.get("STORAGE_FILE", "processed_meetings.json")
_LOCK_FILE = STORAGE_FILE + ".lock"


def _load() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {}
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as exc:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = f"{STORAGE_FILE}.corrupt.{timestamp}"
        logger.critical(
            "processed_meetings.json is corrupted (%s). "
            "Backing up to %s and starting fresh.",
            exc,
            backup,
        )
        shutil.copy2(STORAGE_FILE, backup)
        return {}


def _save(data: dict) -> None:
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_processed(meeting_id: str) -> bool:
    lock = FileLock(_LOCK_FILE)
    with lock:
        data = _load()
        return meeting_id in data


def mark_processed(meeting_id: str, title: str) -> None:
    lock = FileLock(_LOCK_FILE)
    with lock:
        data = _load()
        data[meeting_id] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "title": title,
        }
        _save(data)
