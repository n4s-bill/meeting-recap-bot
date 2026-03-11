import logging
import time
from datetime import datetime

import httpx
import markdown
import nh3
from azure.identity import ClientSecretCredential

import config

logger = logging.getLogger(__name__)

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "strong", "em", "a", "br",
    "blockquote", "code", "pre",
}
_ALLOWED_ATTRIBUTES = {"a": {"href"}}

_credential: ClientSecretCredential | None = None


def _get_credential() -> ClientSecretCredential:
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=config.MS_GRAPH_TENANT_ID,
            client_id=config.MS_GRAPH_CLIENT_ID,
            client_secret=config.MS_GRAPH_CLIENT_SECRET,
        )
    return _credential


def _get_token() -> str:
    cred = _get_credential()
    token = cred.get_token("https://graph.microsoft.com/.default")
    return token.token


def _markdown_to_safe_html(text: str) -> str:
    raw_html = markdown.markdown(text)
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES)


def _format_date(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except (ValueError, AttributeError):
        return date_str


def _build_payload(
    to: list[str],
    cc: list[str],
    subject: str,
    html_body: str,
) -> dict:
    return {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
            "ccRecipients": [
                {"emailAddress": {"address": addr}} for addr in cc
            ],
        },
        "saveToSentItems": True,
    }


def _post_mail(payload: dict) -> None:
    token = _get_token()
    url = GRAPH_SEND_URL.format(sender=config.EMAIL_FROM)
    delays = [1, 4, 16]
    last_exc: Exception | None = None

    for attempt in range(1, 4):
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            logger.error(
                "Microsoft Graph error: %s %s. Attempt %d/3.",
                exc.response.status_code,
                exc.response.text[:200],
                attempt,
            )
            if attempt < 3:
                time.sleep(delays[attempt - 1])
        except Exception as exc:
            last_exc = exc
            logger.error(
                "Microsoft Graph unexpected error: %s. Attempt %d/3.", exc, attempt
            )
            if attempt < 3:
                time.sleep(delays[attempt - 1])

    raise RuntimeError("Microsoft Graph sendMail failed after 3 attempts") from last_exc


def send_recap(
    meeting_id: str,
    title: str,
    date_str: str,
    to: list[str],
    cc: list[str],
    summary_markdown: str,
) -> None:
    formatted_date = _format_date(date_str)
    subject = f"[Meeting Recap] {title} \u2014 {formatted_date}"
    html_body = _markdown_to_safe_html(summary_markdown)
    payload = _build_payload(to=to, cc=cc, subject=subject, html_body=html_body)
    _post_mail(payload)
    logger.info(
        "[%s] Recap email sent for: %s to %d recipients",
        meeting_id,
        title,
        len(to),
    )


def send_failure_notification(meeting_id: str, title: str, date_str: str, error: str) -> None:
    formatted_date = _format_date(date_str)
    subject = f"[Meeting Recap - FAILED] {title} \u2014 {formatted_date}"
    body = (
        "<p>Automatic summarization failed for this meeting. "
        "The transcript was received but could not be processed. "
        "Please generate the summary manually.</p>"
        f"<p><strong>Error:</strong> {error}</p>"
    )
    payload = _build_payload(
        to=[config.EMAIL_CC],
        cc=[],
        subject=subject,
        html_body=body,
    )
    try:
        _post_mail(payload)
        logger.warning(
            "[%s] Failure notification sent to Bill for: %s", meeting_id, title
        )
    except Exception as exc:
        logger.error(
            "[%s] Could not send failure notification: %s", meeting_id, exc
        )
