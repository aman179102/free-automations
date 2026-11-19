from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised when critical configuration is missing or invalid."""
    pass


DEFAULT_GEMINI_MODELS = (
    "gemini-flash-latest",
    "gemini-pro-latest",
)

DEFAULT_GROQ_MODELS = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
)

DEFAULT_OPENROUTER_MODELS = (
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
)


def _model_list(plural: str, singular: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(plural) or os.getenv(singular)
    if not raw:
        return default
    values = tuple(v.strip() for v in raw.split(",") if v.strip())
    return values or default


@dataclass(frozen=True)
class Settings:
    # Personal Branding & Author Details
    author_name: str = "Aman Kumar"
    author_handle: str = "@aman179102"
    author_title: str = "Systems & AI Engineer"
    github_user: str = "aman179102"
    portfolio_url: str = "https://github.com/aman179102"

    # AI API Keys & Fallback Configurations
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_models: tuple[str, ...] = DEFAULT_GEMINI_MODELS
    groq_models: tuple[str, ...] = DEFAULT_GROQ_MODELS
    openrouter_models: tuple[str, ...] = DEFAULT_OPENROUTER_MODELS

    # Telegram Bot (1-Tap Mobile Co-Pilot)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Hacker News Autonomous Authentication
    hn_username: str = ""
    hn_password: str = ""
    hn_cookie: str = ""
    hn_auto_publish: bool = False

    # Tumblr Official API v2 Authentication
    tumblr_consumer_key: str = ""
    tumblr_consumer_secret: str = ""
    tumblr_oauth_token: str = ""
    tumblr_oauth_token_secret: str = ""
    tumblr_blog_name: str = ""
    tumblr_auto_publish: bool = False

    # itch.io Autonomous Engine
    itch_api_key: str = ""
    itch_username: str = ""
    itch_auto_publish: bool = False

    # Runtime & Paths
    timezone: str = "Asia/Kolkata"
    port: int = 10000
    render_external_url: str = ""
    state_dir: Path = field(default_factory=lambda: ROOT / "state")
    output_dir: Path = field(default_factory=lambda: ROOT / "output")

    @property
    def has_ai(self) -> bool:
        """Returns True if at least one AI provider API key is present."""
        return bool(self.gemini_api_key or self.groq_api_key or self.openrouter_api_key)

    @property
    def has_telegram(self) -> bool:
        """Returns True if Telegram credentials are fully set."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def has_tumblr(self) -> bool:
        """Returns True if all Tumblr OAuth credentials and blog name are present."""
        return bool(
            self.tumblr_consumer_key
            and self.tumblr_consumer_secret
            and self.tumblr_oauth_token
            and self.tumblr_oauth_token_secret
            and self.tumblr_blog_name
        )

    @property
    def has_itch(self) -> bool:
        """Returns True if itch.io API key is present."""
        return bool(self.itch_api_key)


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    """Loads application settings from environment variables and optional overrides."""
    overrides = overrides or {}

    def get_str(name: str, alt_names: tuple[str, ...] = (), default: str = "") -> str:
        for n in (name, *alt_names):
            val = os.getenv(n)
            if val is not None and val.strip():
                return val.strip()
        return default

    author_name = str(overrides.get("author_name") or get_str("AUTHOR_NAME", default="Aman Kumar"))
    author_handle = str(overrides.get("author_handle") or get_str("AUTHOR_HANDLE", default="@aman179102"))
    author_title = str(overrides.get("author_title") or get_str("AUTHOR_TITLE", default="Systems & AI Engineer"))
    github_user = str(overrides.get("github_user") or get_str("GITHUB_USER", default="aman179102"))
    portfolio_url = str(overrides.get("portfolio_url") or get_str("PORTFOLIO_URL", default=f"https://github.com/{github_user}"))

    gemini_key = str(overrides.get("gemini_api_key") or get_str("GEMINI_API_KEY"))
    groq_key = str(overrides.get("groq_api_key") or get_str("GROQ_API_KEY"))
    openrouter_key = str(overrides.get("openrouter_api_key") or get_str("OPENROUTER_API_KEY"))

    telegram_bot_token = str(overrides.get("telegram_bot_token") or get_str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id = str(overrides.get("telegram_chat_id") or get_str("TELEGRAM_CHAT_ID"))

    hn_username = str(overrides.get("hn_username") or get_str("HN_USERNAME"))
    hn_password = str(overrides.get("hn_password") or get_str("HN_PASSWORD"))
    hn_cookie = str(overrides.get("hn_cookie") or get_str("HN_COOKIE"))
    raw_auto = overrides.get("hn_auto_publish")
    if raw_auto is None:
        raw_auto = os.getenv("HN_AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
    hn_auto_publish = bool(raw_auto)

    tumblr_consumer_key = str(overrides.get("tumblr_consumer_key") or get_str("TUMBLR_CONSUMER_KEY"))
    tumblr_consumer_secret = str(overrides.get("tumblr_consumer_secret") or get_str("TUMBLR_CONSUMER_SECRET"))
    tumblr_oauth_token = str(overrides.get("tumblr_oauth_token") or get_str("TUMBLR_OAUTH_TOKEN"))
    tumblr_oauth_token_secret = str(overrides.get("tumblr_oauth_token_secret") or get_str("TUMBLR_OAUTH_TOKEN_SECRET"))
    tumblr_blog_name = str(overrides.get("tumblr_blog_name") or get_str("TUMBLR_BLOG_NAME"))
    raw_tumblr_auto = overrides.get("tumblr_auto_publish")
    if raw_tumblr_auto is None:
        raw_tumblr_auto = os.getenv("TUMBLR_AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
    tumblr_auto_publish = bool(raw_tumblr_auto)

    itch_api_key = str(overrides.get("itch_api_key") or get_str("ITCH_API_KEY"))
    itch_username = str(overrides.get("itch_username") or get_str("ITCH_USERNAME", default="aman179102"))
    raw_itch_auto = overrides.get("itch_auto_publish")
    if raw_itch_auto is None:
        raw_itch_auto = os.getenv("ITCH_AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
    itch_auto_publish = bool(raw_itch_auto)

    tz_str = str(overrides.get("timezone") or get_str("TIMEZONE", default="Asia/Kolkata"))
    port = int(overrides.get("port") or int(os.getenv("PORT", "10000")))
    render_external_url = str(overrides.get("render_external_url") or get_str("RENDER_EXTERNAL_URL", default=""))

    state_dir_path = overrides.get("state_dir")
    state_dir = Path(str(state_dir_path)) if state_dir_path else ROOT / "state"

    output_dir_path = overrides.get("output_dir")
    output_dir = Path(str(output_dir_path)) if output_dir_path else ROOT / "output"

    return Settings(
        author_name=author_name,
        author_handle=author_handle,
        author_title=author_title,
        github_user=github_user,
        portfolio_url=portfolio_url,
        gemini_api_key=gemini_key,
        groq_api_key=groq_key,
        openrouter_api_key=openrouter_key,
        gemini_models=_model_list("GEMINI_MODELS", "GEMINI_MODEL", DEFAULT_GEMINI_MODELS),
        groq_models=_model_list("GROQ_MODELS", "GROQ_MODEL", DEFAULT_GROQ_MODELS),
        openrouter_models=_model_list("OPENROUTER_MODELS", "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODELS),
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        hn_username=hn_username,
        hn_password=hn_password,
        hn_cookie=hn_cookie,
        hn_auto_publish=hn_auto_publish,
        tumblr_consumer_key=tumblr_consumer_key,
        tumblr_consumer_secret=tumblr_consumer_secret,
        tumblr_oauth_token=tumblr_oauth_token,
        tumblr_oauth_token_secret=tumblr_oauth_token_secret,
        tumblr_blog_name=tumblr_blog_name,
        tumblr_auto_publish=tumblr_auto_publish,
        itch_api_key=itch_api_key,
        itch_username=itch_username,
        itch_auto_publish=itch_auto_publish,
        timezone=tz_str,
        port=port,
        render_external_url=render_external_url,
        state_dir=state_dir,
        output_dir=output_dir,
    )
