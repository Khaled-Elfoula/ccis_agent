"""Start the CCIS Support Agent on Telegram with long polling."""

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update

import config
from bot.telegram import build_application
import time
import urllib.request

_last_inbound = time.time()

class _Health(BaseHTTPRequestHandler):
    """Answers 'ok' so Render sees an open port, and records when we were last called."""

    def do_GET(self):
        global _last_inbound
        _last_inbound = time.time()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass

def start_self_ping(idle_seconds: int = 600) -> None:
    """If no web request arrived for idle_seconds, request our own public URL.

    Only runs on Render, where RENDER_EXTERNAL_URL is set.
    """
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return

    def loop() -> None:
        while True:
            time.sleep(60)
            if time.time() - _last_inbound >= idle_seconds:
                try:
                    logging.info("self-ping: no web traffic for %s s, requesting %s", idle_seconds, url)
                    urllib.request.urlopen(url, timeout=30).read()
                except Exception:
                    pass

    threading.Thread(target=loop, daemon=True).start()

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
    start_self_ping()

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