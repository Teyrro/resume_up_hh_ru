"""Конфигурация из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Без них сервис не запустится.
REQUIRED_VARS = ("HH_LOGIN_EMAIL", "HH_RESUME_IDS", "IMAP_PASSWORD")
# Без них сервис работает, но без уведомлений и команд в Telegram.
OPTIONAL_VARS = ("TG_BOT_TOKEN", "TG_CHAT_ID")


def missing_vars(env: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Имена незаданных переменных: (обязательные, необязательные). Значения не раскрываются."""
    env = os.environ if env is None else env
    return ([n for n in REQUIRED_VARS if not (env.get(n) or "").strip()],
            [n for n in OPTIONAL_VARS if not (env.get(n) or "").strip()])


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value or ""


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    # hh.ru
    hh_login_email: str
    hh_base_url: str
    resume_ids: list[str]

    # Почта (IMAP) для получения кода
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str
    mail_sender_filter: str
    code_wait_seconds: int

    # Telegram (необязательно)
    tg_bot_token: str
    tg_chat_id: str

    # Расписание и ограничения
    min_interval_minutes: int
    jitter_min_minutes: int
    jitter_max_minutes: int
    max_logins_per_day: int

    # Пути
    data_dir: Path
    headless: bool = True
    timezone: str = "Asia/Novosibirsk"

    profile_dir: Path = field(init=False)
    debug_dir: Path = field(init=False)
    state_file: Path = field(init=False)
    stop_flag: Path = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_dir", self.data_dir / "profile")
        object.__setattr__(self, "debug_dir", self.data_dir / "debug")
        object.__setattr__(self, "state_file", self.data_dir / "state.json")
        object.__setattr__(self, "stop_flag", self.data_dir / "STOPPED")


def load() -> Config:
    required, _ = missing_vars()
    if required:
        raise RuntimeError(f"Не заданы обязательные переменные окружения: {', '.join(required)}")
    email = _env("HH_LOGIN_EMAIL", required=True)
    resume_ids = [r.strip() for r in _env("HH_RESUME_IDS", "").split(",") if r.strip()]
    return Config(
        hh_login_email=email,
        hh_base_url=_env("HH_BASE_URL", "https://hh.ru").rstrip("/"),
        resume_ids=resume_ids,
        imap_host=_env("IMAP_HOST", "imap.gmail.com"),
        imap_port=_int("IMAP_PORT", 993),
        imap_user=_env("IMAP_USER", email),
        imap_password=_env("IMAP_PASSWORD", required=True),
        imap_folder=_env("IMAP_FOLDER", "INBOX"),
        mail_sender_filter=_env("MAIL_SENDER_FILTER", "hh.ru"),
        code_wait_seconds=_int("CODE_WAIT_SECONDS", 180),
        tg_bot_token=_env("TG_BOT_TOKEN", ""),
        tg_chat_id=_env("TG_CHAT_ID", ""),
        min_interval_minutes=_int("MIN_INTERVAL_MINUTES", 240),
        jitter_min_minutes=_int("JITTER_MIN_MINUTES", 5),
        jitter_max_minutes=_int("JITTER_MAX_MINUTES", 180),
        max_logins_per_day=_int("MAX_LOGINS_PER_DAY", 3),
        data_dir=Path(_env("DATA_DIR", "/data")),
        headless=_env("HEADLESS", "1") not in ("0", "false", "False"),
        timezone=_env("TZ", "Asia/Novosibirsk"),
    )
