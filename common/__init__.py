"""Common foundational modules for the free-automations platform."""

from common.config import Settings, load_settings, ConfigError
from common.ai import generate_json, generate_text, test_ai_connectivity
from common.telegram import (
    send_telegram_message,
    send_telegram_photo,
    test_telegram_ping,
    setup_telegram_webhook,
    build_inline_keyboard,
    build_hn_button,
    build_mailto_button,
    build_x_intent_button,
    build_github_button,
    format_hiring_lead_message,
    format_discussion_message,
    format_showcase_message,
)
from common.state import (
    load_state,
    save_state,
    is_seen,
    mark_seen,
    record_action,
    get_last_run_time,
    default_state,
)

__all__ = [
    "Settings",
    "load_settings",
    "ConfigError",
    "generate_json",
    "generate_text",
    "test_ai_connectivity",
    "send_telegram_message",
    "send_telegram_photo",
    "test_telegram_ping",
    "setup_telegram_webhook",
    "build_inline_keyboard",
    "build_hn_button",
    "build_mailto_button",
    "build_x_intent_button",
    "build_github_button",
    "format_hiring_lead_message",
    "format_discussion_message",
    "format_showcase_message",
    "load_state",
    "save_state",
    "is_seen",
    "mark_seen",
    "record_action",
    "get_last_run_time",
    "default_state",
]
