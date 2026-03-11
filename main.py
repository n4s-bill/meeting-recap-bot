import logging
import sys

import uvicorn

import config
import meeting_type_config
import summarizer
from webhook_server import app


def _configure_logging() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    _configure_logging()
    logger = logging.getLogger(__name__)

    config.validate_config()

    try:
        summarizer.load_instructions()
    except FileNotFoundError:
        logger.critical(
            "instructions.md not found. Create the file before starting the service."
        )
        sys.exit(1)

    meeting_type_config.load_meeting_types()

    logger.info(
        "Webhook server started on %s:%d", config.WEBHOOK_HOST, config.WEBHOOK_PORT
    )

    uvicorn.run(app, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
