from typing import Optional
from pydantic import BaseModel, field_validator


class Participant(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    permission: Optional[str] = None


def _parse_participants_string(raw: str) -> list[dict]:
    """Parse Zapier's newline-delimited participant text into dicts.

    Expected format (participants separated by blank lines):
        email: alice@co.com
        name: Alice
        permission: None

        email: bob@co.com
        name: Bob
        permission: None
    """
    results = []
    blocks = raw.strip().split("\n\n")
    for block in blocks:
        entry: dict[str, Optional[str]] = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            value = value.strip()
            if value.lower() == "none" or value == "":
                value = None
            entry[key.strip().lower()] = value
        if entry.get("email"):
            results.append(entry)
    return results


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    meeting_id: str
    title: str
    date: Optional[str] = None
    participants: Optional[list[Participant]] = None
    transcript: str

    @field_validator("participants", mode="before")
    @classmethod
    def coerce_participants(cls, v):
        if isinstance(v, str):
            return _parse_participants_string(v)
        return v
