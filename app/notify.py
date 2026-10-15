"""Уведомления в Telegram и команды от владельца (необязательно)."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def api(self, method: str, params: dict, timeout: float = 15) -> object:
        """Вызов Bot API; при ошибке бросает исключение."""
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        if not body.get("ok"):
            raise RuntimeError(f"Telegram ответил ошибкой: {body}")
        return body["result"]

    def send(self, text: str) -> None:
        log.info("NOTIFY: %s", text)
        if not self.enabled:
            return
        try:
            self.api("sendMessage", {
                "chat_id": self.chat_id,
                "text": f"hh-autoraise: {text}",
                "disable_web_page_preview": "true",
            })
        except Exception as e:  # уведомления не должны ронять сервис
            log.warning("Не удалось отправить в Telegram: %s", e)


class CommandListener(threading.Thread):
    """Фоновый long polling getUpdates: выполняет команды только из чата TG_CHAT_ID."""

    def __init__(self, notifier: Notifier, handlers: dict[str, Callable[[], str]]) -> None:
        super().__init__(name="tg-commands", daemon=True)
        self.notifier = notifier
        self.handlers = handlers

    def run(self) -> None:
        offset = self._skip_old()
        while True:
            try:
                updates = self.notifier.api("getUpdates", {
                    "offset": offset or 0,
                    "timeout": 50,
                    "allowed_updates": '["message"]',
                }, timeout=60)
            except Exception as e:
                log.warning("Не удалось получить команды из Telegram: %s", e)
                time.sleep(30)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                self.handle(upd.get("message") or {})

    def _skip_old(self) -> int | None:
        """Пропустить команды, пришедшие до запуска сервиса."""
        try:
            last = self.notifier.api("getUpdates", {"offset": -1, "timeout": 0})
        except Exception as e:
            log.warning("Telegram getUpdates: %s", e)
            return None
        return last[-1]["update_id"] + 1 if last else None

    def handle(self, message: dict) -> None:
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(self.notifier.chat_id):
            if chat_id:
                log.warning("Команда из чужого чата %s проигнорирована", chat_id)
            return
        words = (message.get("text") or "").split()
        if not words or not words[0].startswith("/"):
            return
        command = words[0].split("@")[0].lower()
        handler = self.handlers.get(command)
        if handler is None:
            self.notifier.send("Команды: " + ", ".join(self.handlers)
                               + (" — подробнее /help" if "/help" in self.handlers else ""))
            return
        log.info("Команда из Telegram: %s", command)
        self.notifier.send(handler())
