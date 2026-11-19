from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from common.config import Settings, load_settings
from common.telegram import setup_telegram_webhook, send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("free-automations-server")


def _dispatch_automation_job(action: str, settings: Settings, extra: dict | None = None) -> bool:
    """Dispatches requested automation runner in a worker thread."""
    try:
        from automations.hn import runner
    except ImportError:
        logger.warning("automations.hn.runner not yet available or failed to import.")
        return False

    try:
        if action in ("hiring", "hn_hiring"):
            if hasattr(runner, "run_hiring_radar"):
                runner.run_hiring_radar(settings)
                return True
        elif action in ("discussion", "hn_discussion"):
            if hasattr(runner, "run_discussion"):
                runner.run_discussion(settings)
                return True
        elif action in ("showcase", "hn_showcase"):
            if hasattr(runner, "run_showcase"):
                runner.run_showcase(settings)
                return True
        elif action in ("all", "hn_all"):
            if hasattr(runner, "run_all"):
                runner.run_all(settings)
                return True
        elif hasattr(runner, "dispatch_action"):
            return runner.dispatch_action(action, settings, extra or {})
    except Exception as exc:
        logger.error("Error executing automation job %s: %s", action, exc)
        return False

    logger.warning("Unrecognized action or runner method: %s", action)
    return False


class AutomationHandler(BaseHTTPRequestHandler):
    settings: Settings = None  # Injected at runtime

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("", "/health", "/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {
                "status": "healthy",
                "service": "free-automations-platform",
                "author": self.settings.author_name if self.settings else "Aman Kumar",
                "handle": self.settings.author_handle if self.settings else "@aman179102",
                "github": self.settings.portfolio_url if self.settings else "https://github.com/aman179102",
                "ai_available": self.settings.has_ai if self.settings else False,
                "telegram_configured": self.settings.has_telegram if self.settings else False,
                "endpoints": [
                    "GET  /health",
                    "POST /trigger?type=hiring|discussion|showcase|all",
                    "POST /trigger/hn/hiring",
                    "POST /trigger/hn/discussion",
                    "POST /trigger/hn/showcase",
                    "POST /trigger/hn/all",
                    "POST /webhook",
                ],
                "time_utc": datetime.now(timezone.utc).isoformat(),
            }
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}')

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        # Parse request body if present
        body_data: dict = {}
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            try:
                raw_body = self.rfile.read(content_length).decode("utf-8")
                body_data = json.loads(raw_body) if raw_body else {}
            except Exception:
                pass

        action: str | None = None

        if path in ("/trigger", "/trigger/"):
            action = query.get("type", [body_data.get("type")])[0]
        elif path in ("/trigger/hn/hiring", "/trigger_hiring", "/hiring"):
            action = "hiring"
        elif path in ("/trigger/hn/discussion", "/trigger_discussion", "/discussion"):
            action = "discussion"
        elif path in ("/trigger/hn/showcase", "/trigger_showcase", "/showcase"):
            action = "showcase"
        elif path in ("/trigger/hn/all", "/trigger/all", "/trigger_all"):
            action = "all"
        elif path in ("/webhook", "/telegram_webhook"):
            # Telegram Webhook endpoint
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

            if body_data:
                # Process Telegram message command in background
                message = body_data.get("message", {})
                text = message.get("text", "").strip()
                if text.startswith("/hiring"):
                    threading.Thread(target=_dispatch_automation_job, args=("hiring", self.settings), daemon=True).start()
                elif text.startswith("/discussion"):
                    threading.Thread(target=_dispatch_automation_job, args=("discussion", self.settings), daemon=True).start()
                elif text.startswith("/showcase"):
                    threading.Thread(target=_dispatch_automation_job, args=("showcase", self.settings), daemon=True).start()
                elif text.startswith("/all"):
                    threading.Thread(target=_dispatch_automation_job, args=("all", self.settings), daemon=True).start()
                elif text.startswith("/start") or text.startswith("/help"):
                    help_text = (
                        "🤖 <b>Free-Automations Mobile Co-Pilot</b>\n\n"
                        "Available commands:\n"
                        "• /hiring - Scan HN Who is Hiring & draft remote outreach\n"
                        "• /discussion - Find Ask HN questions & draft technical answers\n"
                        "• /showcase - Draft Show HN posts for open-source systems\n"
                        "• /all - Run full automation scan"
                    )
                    send_telegram_message(self.settings, help_text)
            return

        if action:
            logger.info("Triggering action '%s' via HTTP webhook...", action)
            # Dispatch in background worker thread so HTTP response is snappy
            t = threading.Thread(target=_dispatch_automation_job, args=(action, self.settings, body_data), daemon=True)
            t.start()

            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "status": "accepted",
                "action": action,
                "message": f"Automation '{action}' queued for execution.",
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Unknown webhook route"}')

    def log_message(self, format: str, *args: object) -> None:
        # Suppress routine health check log spam
        if args and str(args[0]).startswith("GET /health"):
            return
        logger.info("%s - %s", self.address_string(), format % args)


def run_server(port: int | None = None) -> None:
    settings = load_settings()
    server_port = port or settings.port
    AutomationHandler.settings = settings

    if settings.render_external_url:
        webhook_url = f"{settings.render_external_url.rstrip('/')}/webhook"
        logger.info("Registering Telegram Webhook at: %s", webhook_url)
        setup_telegram_webhook(settings, webhook_url)

    server_address = ("0.0.0.0", server_port)
    httpd = ThreadingHTTPServer(server_address, AutomationHandler)
    logger.info("Free-Automations HTTP Server running on 0.0.0.0:%d", server_port)
    logger.info("Healthcheck available at http://0.0.0.0:%d/health", server_port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down HTTP server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
