"""Start the CCIS Support Agent on Telegram with long polling."""

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update

import config
from bot.telegram import build_application


class _Health(BaseHTTPRequestHandler):
    """Answers 'ok' so Render sees an open port."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def start_health_server() -> None:
    """Open the PORT Render expects. Does nothing when PORT is not set (local runs)."""
    port = int(os.environ.get("PORT", "0"))
    if port:
        server = HTTPServer(("0.0.0.0", port), _Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> None:
    """Check the configuration and run the bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # These libraries print full request URLs, which contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    start_health_server()

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