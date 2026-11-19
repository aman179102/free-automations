from __future__ import annotations

import html
import json
import logging
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from common.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def build_inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    """Wraps rows of button dicts into a Telegram inline_keyboard structure, filtering invalid URLs."""
    valid_rows: list[list[dict[str, str]]] = []
    for row in rows:
        valid_row: list[dict[str, str]] = []
        for btn in row:
            btn_url = btn.get("url", "")
            # Telegram inline keyboard only accepts http://, https://, or tg://
            if btn_url.startswith(("http://", "https://", "tg://")):
                valid_row.append(btn)
        if valid_row:
            valid_rows.append(valid_row)
    return {"inline_keyboard": valid_rows}


def build_hn_button(item_id: int | str, text: str = "🧡 Open on Hacker News") -> dict[str, str]:
    """1-Tap mobile link directly to the Hacker News item discussion."""
    return {"text": text, "url": f"https://news.ycombinator.com/item?id={item_id}"}


def build_mailto_button(
    email: str,
    subject: str = "",
    body: str = "",
    text: str = "✉️ 1-Tap Gmail Pitch",
) -> dict[str, str]:
    """1-Tap webmail composer intent (HTTPS compliant with Telegram bot inline keyboards)."""
    params: dict[str, str] = {"view": "cm", "fs": "1", "to": email}
    if subject:
        params["su"] = subject
    if body:
        params["body"] = body

    query = urllib.parse.urlencode(params)
    url = f"https://mail.google.com/mail/?{query}"
    return {"text": text, "url": url}


def build_x_intent_button(
    text: str,
    hashtags: list[str] | None = None,
    label: str = "🚀 1-Tap Post on X",
) -> dict[str, str]:
    """Builds 1-Tap mobile intent for X (Twitter) posting with separated hashtags."""
    params: dict[str, str] = {"text": text}
    if hashtags:
        params["hashtags"] = ",".join(hashtags)
    url = f"https://x.com/intent/post?{urllib.parse.urlencode(params)}"
    return {"text": label, "url": url}


def build_github_button(
    portfolio_url: str = "https://github.com/aman179102",
    text: str = "🐙 Aman's GitHub",
) -> dict[str, str]:
    """Link button to Aman's GitHub profile or repo."""
    return {"text": text, "url": portfolio_url}


def send_telegram_message(
    settings: Settings,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "HTML",
    disable_preview: bool = False,
) -> bool:
    """
    Dispatches an HTML formatted text message with optional inline keyboard to Telegram.
    Guarantees message fits Telegram's 4096-char ceiling.
    """
    bot_token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()

    if not bot_token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing from settings.")
        print("❌ Telegram credentials missing in settings.")
        return False

    # Safety ceiling truncation (Telegram max is 4096)
    if len(text) > 4000:
        logger.warning("Message length %d exceeds safe limit. Truncating.", len(text))
        text = text[:3950] + "\n\n<i>[Message truncated...]</i>"

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Successfully dispatched message to Telegram!")
                return True
            else:
                logger.error("Telegram sendMessage failed (%d): %s", resp.status_code, resp.text)
                print(f"❌ Telegram API Error ({resp.status_code}): {resp.text}")
                return False
    except Exception as exc:
        logger.error("Telegram dispatch error: %s", exc)
        print(f"❌ Connection to Telegram failed: {exc}")
        return False


def send_telegram_photo(
    settings: Settings,
    photo_path: Path | str,
    caption: str = "",
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """Dispatches a photo with caption and inline keyboard to Telegram."""
    bot_token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()

    if not bot_token or not chat_id:
        return False

    path = Path(photo_path)
    if not path.is_file():
        logger.warning("Photo file does not exist: %s. Falling back to text message.", photo_path)
        return send_telegram_message(settings, caption, reply_markup=reply_markup, parse_mode=parse_mode)

    # Telegram caption max is 1024 chars
    if len(caption) > 1020:
        caption = caption[:1000] + "..."

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto"
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    try:
        with open(path, "rb") as f:
            files = {"photo": (path.name, f, "image/jpeg")}
            with httpx.Client(timeout=25.0) as client:
                resp = client.post(url, data=data, files=files)
                if resp.status_code == 200:
                    return True
                logger.warning("Telegram sendPhoto failed (%d): %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Failed to send photo: %s. Falling back to text.", exc)

    return send_telegram_message(settings, caption, reply_markup=reply_markup, parse_mode=parse_mode)


def test_telegram_ping(settings: Settings) -> bool:
    """Sends a verification ping to the configured Telegram chat."""
    bot_token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()

    if not bot_token or not chat_id:
        print("❌ Cannot test Telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in configuration.")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": (
            "🔔 <b>[Free Automations Platform Connected]</b>\n\n"
            f"Hello <b>{html.escape(settings.author_name)}</b> ({html.escape(settings.author_handle)})!\n\n"
            "Your Telegram 1-Tap Mobile Co-Pilot is fully online. "
            "High-signal remote hiring leads, Ask HN engineering responses, and project showcases "
            "will be delivered directly here with 1-tap mobile action buttons."
        ),
        "parse_mode": "HTML",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                print("✅ Telegram connection verified! Test ping sent successfully.")
                return True
            print(f"❌ Telegram API returned error {resp.status_code}: {resp.text}")
            return False
    except Exception as exc:
        print(f"❌ Connection failed: {exc}")
        return False


def setup_telegram_webhook(settings: Settings, webhook_url: str) -> bool:
    """Registers a webhook URL with the Telegram Bot API."""
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token or not webhook_url:
        return False

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/setWebhook"
    payload = {
        "url": webhook_url,
        "drop_pending_updates": True,
        "allowed_updates": ["message", "callback_query"],
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Telegram webhook set to: %s", webhook_url)
                return True
            logger.warning("Failed to set webhook: %s", resp.text)
            return False
    except Exception as exc:
        logger.error("Error setting webhook: %s", exc)
        return False


# ------------------------------------------------------------------------------
# Specialized Message Formatters for Automation Workflows
# ------------------------------------------------------------------------------

def format_hiring_lead_message(
    company: str,
    role: str,
    contact_email: str,
    pitch_body: str,
    hn_id: int | str,
    apply_url: str = "",
    tech_stack: list[str] | None = None,
    portfolio_url: str = "https://github.com/aman179102",
) -> tuple[str, dict[str, Any]]:
    """Formats a remote hiring lead message with 1-tap email and HN intent buttons."""
    escaped_company = html.escape(company)
    escaped_role = html.escape(role)
    escaped_pitch = html.escape(pitch_body)
    stack_str = ", ".join(tech_stack) if tech_stack else "Python, Golang, FastAPI"

    msg = (
        f"🎯 <b>[HN HIRING RADAR] 100% Remote High-Signal Lead</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Company:</b> {escaped_company}\n"
        f"💼 <b>Role:</b> {escaped_role}\n"
        f"🛠️ <b>Stack:</b> {html.escape(stack_str)}\n"
    )
    if contact_email:
        msg += f"📧 <b>Contact:</b> <code>{html.escape(contact_email)}</code>\n"
    if apply_url:
        msg += f"🔗 <b>Apply Link:</b> {html.escape(apply_url)}\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Tailored High-Signal Pitch (tap to copy):</b>\n"
        f"<code>{escaped_pitch}</code>\n\n"
        f"📲 <i>Tap <b>[✉️ 1-Tap Email Founder]</b> below to open Gmail/Mail with pre-drafted pitch!</i>"
    )

    row1 = []
    if contact_email:
        subject = f"Systems & AI Engineer ({role}) - Aman Kumar"
        row1.append(build_mailto_button(contact_email, subject=subject, body=pitch_body))
    elif apply_url:
        row1.append({"text": "🌐 Direct Apply Link", "url": apply_url})

    row2 = [
        build_hn_button(hn_id, "🧡 View on Hacker News"),
        build_github_button(portfolio_url),
    ]

    buttons = [row1, row2] if row1 else [row2]
    return msg, build_inline_keyboard(buttons)


def format_discussion_message(
    title: str,
    hn_id: int | str,
    response_text: str,
    points: int = 0,
    author: str = "",
    portfolio_url: str = "https://github.com/aman179102",
) -> tuple[str, dict[str, Any]]:
    """Formats an Ask HN technical architecture response draft."""
    escaped_title = html.escape(title)
    escaped_response = html.escape(response_text)

    msg = (
        f"💡 <b>[ASK HN ARCHITECTURE CO-PILOT]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Question:</b> {escaped_title}\n"
        f"⭐ <b>Points:</b> {points} | <b>Author:</b> {html.escape(author or 'community')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Prepared Senior Engineering Response:</b>\n"
        f"<code>{escaped_response}</code>\n\n"
        f"📲 <i>Tap <b>[🧡 Open HN Thread]</b> below to post your response in 1 tap!</i>"
    )

    keyboard = build_inline_keyboard([
        [build_hn_button(hn_id, "🧡 Open HN Thread to Reply")],
        [build_github_button(portfolio_url)],
    ])
    return msg, keyboard


def format_showcase_message(
    project_name: str,
    tagline: str,
    github_url: str,
    post_text: str,
    demo_url: str = "",
) -> tuple[str, dict[str, Any]]:
    """Formats a Show HN project draft for open-source systems projects."""
    escaped_name = html.escape(project_name)
    escaped_tagline = html.escape(tagline)
    escaped_post = html.escape(post_text)

    msg = (
        f"🚀 <b>[SHOW HN PROJECT SHOWCASE]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Project:</b> {escaped_name}\n"
        f"⚡ <b>Tagline:</b> {escaped_tagline}\n"
        f"🐙 <b>Repo:</b> {html.escape(github_url)}\n"
    )
    if demo_url:
        msg += f"🌐 <b>Demo:</b> {html.escape(demo_url)}\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Show HN Draft Post:</b>\n"
        f"<code>{escaped_post}</code>\n\n"
        f"📲 <i>Ready to submit to Hacker News or publish across social channels.</i>"
    )

    row1 = [{"text": "🐙 Open Repository", "url": github_url}]
    if demo_url:
        row1.append({"text": "🌐 Live Demo", "url": demo_url})

    submit_hn_url = f"https://news.ycombinator.com/submitlink?u={urllib.parse.quote(github_url)}&t={urllib.parse.quote(f'Show HN: {project_name} – {tagline}')}"
    row2 = [{"text": "🧡 1-Tap Submit to Show HN", "url": submit_hn_url}]

    keyboard = build_inline_keyboard([row1, row2])
    return msg, keyboard
