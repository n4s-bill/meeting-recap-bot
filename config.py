import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Required ────────────────────────────────────────────────────────────────

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
MS_GRAPH_CLIENT_ID: str = os.environ.get("MS_GRAPH_CLIENT_ID", "")
MS_GRAPH_CLIENT_SECRET: str = os.environ.get("MS_GRAPH_CLIENT_SECRET", "")
MS_GRAPH_TENANT_ID: str = os.environ.get("MS_GRAPH_TENANT_ID", "")
EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")

# ─── Optional ────────────────────────────────────────────────────────────────

EMAIL_CC: str = os.environ.get("EMAIL_CC", "bill.johnson@scribendi.com")
EMAIL_MODE: str = os.environ.get("EMAIL_MODE", "send").lower()
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o")
WEBHOOK_HOST: str = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.environ.get("WEBHOOK_PORT", "8000"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

_MAX_TRANSCRIPT_CHARS_RAW = os.environ.get("MAX_TRANSCRIPT_CHARS", "100000")
MAX_TRANSCRIPT_CHARS: int = int(_MAX_TRANSCRIPT_CHARS_RAW)

_REQUIRED = {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "MS_GRAPH_CLIENT_ID": MS_GRAPH_CLIENT_ID,
    "MS_GRAPH_CLIENT_SECRET": MS_GRAPH_CLIENT_SECRET,
    "MS_GRAPH_TENANT_ID": MS_GRAPH_TENANT_ID,
    "EMAIL_FROM": EMAIL_FROM,
    "WEBHOOK_SECRET": WEBHOOK_SECRET,
}


def validate_config() -> None:
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in all required values."
        )
