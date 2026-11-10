"""Start the CCIS Support Agent on Telegram with long polling."""

import logging

from telegram import Update

import config
from bot.telegram import build_application


def main() -> None:
    """Check the configuration and run the bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN),
            ("GEMINI_API_KEY", config.GEMINI_API_KEY),
            ("MODEL", config.MODEL),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")

    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
