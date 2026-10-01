"""Central configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "")
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'urbex_crm.db'}")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_user_ids: set[int] = field(default_factory=lambda: _ids("TELEGRAM_ALLOWED_USER_IDS"))

    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")
    xai_api_key: str = os.getenv("XAI_API_KEY", "")
    xai_model: str = os.getenv("XAI_MODEL", "grok-4")
    openai_compatible_base_url: str = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")
    openai_compatible_api_key: str = os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
    openai_compatible_model: str = os.getenv("OPENAI_COMPATIBLE_MODEL", "")

    mail_backend: str = os.getenv("MAIL_BACKEND", "gmail_api")
    gmail_credentials_file: Path = Path(os.getenv("GMAIL_CREDENTIALS_FILE", ROOT / "config" / "gmail_credentials.json"))
    gmail_token_file: Path = Path(os.getenv("GMAIL_TOKEN_FILE", ROOT / "config" / "gmail_token.json"))
    from_name: str = os.getenv("FROM_NAME", "")
    from_email: str = os.getenv("FROM_EMAIL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _int("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")

    apollo_api_key: str = os.getenv("APOLLO_API_KEY", "")
    hunter_api_key: str = os.getenv("HUNTER_API_KEY", "")
    min_contacts_per_company: int = _int("MIN_CONTACTS_PER_COMPANY", 3)
    smtp_verify_guesses: bool = _bool("SMTP_VERIFY_GUESSES", False)

    send_timezone: str = os.getenv("SEND_TIMEZONE", "Europe/Madrid")
    send_window_start: str = os.getenv("SEND_WINDOW_START", "09:00")
    send_window_end: str = os.getenv("SEND_WINDOW_END", "18:00")
    send_weekdays_only: bool = _bool("SEND_WEEKDAYS_ONLY", True)
    daily_send_cap: int = _int("DAILY_SEND_CAP", 40)
    min_seconds_between_sends: int = _int("MIN_SECONDS_BETWEEN_SENDS", 90)
    auto_approve_followups: bool = _bool("AUTO_APPROVE_FOLLOWUPS", True)
    auto_approve_first_touch: bool = _bool("AUTO_APPROVE_FIRST_TOUCH", False)

    sender_full_name: str = os.getenv("SENDER_FULL_NAME", "")
    sender_title: str = os.getenv("SENDER_TITLE", "")
    sender_phone: str = os.getenv("SENDER_PHONE", "")
    sender_linkedin: str = os.getenv("SENDER_LINKEDIN", "")
    company_website: str = os.getenv("COMPANY_WEBSITE", "")
    company_postal_address: str = os.getenv("COMPANY_POSTAL_ADDRESS", "")

    templates_dir: Path = ROOT / "templates"
    pitch_file: Path = ROOT / "config" / "pitch.yaml"


settings = Settings()
