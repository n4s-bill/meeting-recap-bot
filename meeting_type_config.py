import json
import logging
import os

logger = logging.getLogger(__name__)

MEETING_TYPES_FILE = os.environ.get("MEETING_TYPES_FILE", "meeting_types.json")

_meeting_types: dict[str, list[str]] | None = None


def load_meeting_types() -> None:
    global _meeting_types
    _meeting_types = _read_meeting_types()


def _read_meeting_types() -> dict[str, list[str]]:
    if not os.path.exists(MEETING_TYPES_FILE):
        logger.debug("meeting_types.json not found; distro list tier disabled.")
        return {}
    try:
        with open(MEETING_TYPES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(
                "meeting_types.json is not a JSON object; distro list tier disabled."
            )
            return {}
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Failed to parse meeting_types.json (%s); distro list tier disabled.", exc
        )
        return {}


def _get_meeting_types() -> dict[str, list[str]]:
    global _meeting_types
    if _meeting_types is None:
        _meeting_types = _read_meeting_types()
    return _meeting_types


def find_distro_list(title: str) -> list[str]:
    """Return the first matching distro list for the given meeting title, or []."""
    meeting_types = _get_meeting_types()
    title_lower = title.lower()
    for key, recipients in meeting_types.items():
        if key.lower() in title_lower:
            return list(recipients)
    return []
