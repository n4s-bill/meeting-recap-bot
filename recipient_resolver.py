import re
import logging
from dataclasses import dataclass, field

import config
from meeting_type_config import find_distro_list

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ResolvedRecipients:
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)


def _normalize(email: str) -> str:
    return email.strip().lower()


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _clean_emails(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for e in raw:
        normalized = _normalize(e)
        if normalized and _is_valid_email(normalized) and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def resolve(title: str, participants: list[str] | None) -> ResolvedRecipients:
    bill = _normalize(config.EMAIL_CC)

    # Tier 1: payload participants
    valid_participants = _clean_emails(participants or [])
    if valid_participants:
        to = valid_participants
        cc = [] if bill in to else [bill]
        logger.info(
            "Recipients resolved via payload: to=%s, cc=%s", to, cc
        )
        return ResolvedRecipients(to=to, cc=cc)

    # Tier 2: meeting-type distro list
    distro = _clean_emails(find_distro_list(title))
    if distro:
        to = distro
        cc = [] if bill in to else [bill]
        logger.info(
            "Recipients resolved via distro list: to=%s, cc=%s", to, cc
        )
        return ResolvedRecipients(to=to, cc=cc)

    # Tier 3: Bill fallback
    logger.info("Recipients resolved via fallback: to=[%s], cc=[]", bill)
    return ResolvedRecipients(to=[bill], cc=[])
