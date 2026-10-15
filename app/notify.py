"""Уведомления в Telegram (необязательно)."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> None:
        log.info("NOTIFY: %s", text)
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": f"hh-autoraise: {text}",
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=15) as resp:
                body = json.loads(resp.read().decode())
                if not body.get("ok"):
                    log.warning("Telegram ответил ошибкой: %s", body)
        except Exception as e:  # уведомления не должны ронять сервис
            log.warning("Не удалось отправить в Telegram: %s", e)
