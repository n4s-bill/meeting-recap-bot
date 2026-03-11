import logging
import time

from openai import OpenAI, RateLimitError, APIStatusError

import config

logger = logging.getLogger(__name__)

_instructions: str | None = None
_openai_client: OpenAI | None = None

INSTRUCTIONS_FILE = "instructions.md"


def load_instructions() -> str:
    global _instructions
    with open(INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
        _instructions = f.read()
    return _instructions


def _get_instructions() -> str:
    global _instructions
    if _instructions is None:
        _instructions = load_instructions()
    return _instructions


def _get_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai_client


def _apply_size_policy(meeting_id: str, title: str, transcript: str) -> str:
    threshold = config.MAX_TRANSCRIPT_CHARS
    if len(transcript) <= threshold:
        return transcript
    logger.warning(
        "[%s] Transcript for '%s' is %d chars (threshold: %d). Truncating.",
        meeting_id,
        title,
        len(transcript),
        threshold,
    )
    return transcript[:threshold] + "\n\n[Transcript truncated due to size limit]"


def generate_summary(meeting_id: str, title: str, transcript: str) -> str:
    """Summarize a meeting transcript using OpenAI. Raises on all-retries failure."""
    transcript = _apply_size_policy(meeting_id, title, transcript)
    instructions = _get_instructions()
    client = _get_client()

    user_message = (
        "generate meeting summary for this as per the instructions without citations\n\n"
        + transcript
    )

    delays = [1, 4, 16]
    last_exc: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_message},
                ],
            )
            tokens_used = (
                response.usage.total_tokens if response.usage else "unknown"
            )
            logger.info(
                "[%s] Summary generated for: %s (%s tokens used)",
                meeting_id,
                title,
                tokens_used,
            )
            return response.choices[0].message.content or ""

        except (RateLimitError, APIStatusError) as exc:
            last_exc = exc
            logger.error(
                "[%s] OpenAI API error for '%s': %s. Attempt %d/3.",
                meeting_id,
                title,
                exc,
                attempt,
            )
            if attempt < 3:
                time.sleep(delays[attempt - 1])
        except Exception as exc:
            last_exc = exc
            logger.error(
                "[%s] Unexpected OpenAI error for '%s': %s. Attempt %d/3.",
                meeting_id,
                title,
                exc,
                attempt,
            )
            if attempt < 3:
                time.sleep(delays[attempt - 1])

    raise RuntimeError(
        f"OpenAI summarization failed after 3 attempts for meeting '{title}'"
    ) from last_exc
