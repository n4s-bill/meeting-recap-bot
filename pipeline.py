import logging
from dataclasses import dataclass
from enum import Enum

import config
import emailer
import recipient_resolver
import storage
import summarizer
from models import WebhookPayload

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass
class ProcessingResult:
    status: ProcessingStatus
    error: str | None = None


def process_meeting(payload: WebhookPayload) -> ProcessingResult:
    meeting_id = payload.meeting_id
    title = payload.title

    # Step 1: Deduplication check
    if storage.is_processed(meeting_id):
        logger.info("[%s] Skipping duplicate meeting: %s", meeting_id, title)
        return ProcessingResult(status=ProcessingStatus.DUPLICATE)

    # Step 2: Resolve recipients
    participant_emails = [p.email for p in (payload.participants or []) if p.email]
    resolved = recipient_resolver.resolve(
        title=title,
        participants=participant_emails,
    )
    logger.info(
        "[%s] Recipients resolved: to=%s, cc=%s",
        meeting_id,
        resolved.to,
        resolved.cc,
    )

    # Step 3: Apply transcript size policy (handled inside summarizer)
    transcript = payload.transcript

    # Step 4: Summarize
    try:
        summary = summarizer.generate_summary(
            meeting_id=meeting_id,
            title=title,
            transcript=transcript,
        )
    except Exception as exc:
        logger.error(
            "[%s] Summarization failed for '%s': %s", meeting_id, title, exc
        )
        emailer.send_failure_notification(
            meeting_id=meeting_id,
            title=title,
            date_str=payload.date,
            error=str(exc),
        )
        return ProcessingResult(status=ProcessingStatus.FAILED, error=str(exc))

    # Step 5: Send email
    try:
        emailer.send_recap(
            meeting_id=meeting_id,
            title=title,
            date_str=payload.date,
            to=resolved.to,
            cc=resolved.cc,
            summary_markdown=summary,
        )
    except Exception as exc:
        logger.error(
            "[%s] Email send failed for '%s': %s", meeting_id, title, exc
        )
        return ProcessingResult(status=ProcessingStatus.FAILED, error=str(exc))

    # Step 6: Mark processed only after successful email
    storage.mark_processed(meeting_id=meeting_id, title=title)
    logger.info("[%s] Meeting marked processed: %s", meeting_id, title)

    return ProcessingResult(status=ProcessingStatus.SUCCESS)
