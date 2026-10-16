"""Уведомления в Telegram и команды от владельца (необязательно)."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Reply:
    """Ответ бота: текст, inline-кнопки и всплывающая подсказка (для нажатий кнопок)."""

    text: str
    markup: dict | None = None
    toast: str = ""


Handler = Callable[[], "str | Reply"]
Callback = Callable[[str], "str | Reply"]
TextInput = Callable[[str], "str | Reply"]

# Уровни сообщений и какие из них отправляются в каждом режиме уведомлений.
# «reply» — ответы на команды и кнопки, отправляются всегда.
STATUS, INFO, ERROR, REPLY = "status", "info", "error", "reply"
MODE_LEVELS = {
    "all": {STATUS, INFO, ERROR, REPLY},
    "normal": {INFO, ERROR, REPLY},
    "errors": {ERROR, REPLY},
}


class Notifier:
    def __init__(self, bot_token: str = "", chat_id: str = "",
                 mode: Callable[[], str] | None = None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.mode = mode or (lambda: "normal")

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

    def allowed(self, level: str) -> bool:
        try:
            mode = self.mode()
        except Exception:
            mode = "normal"
        return level in MODE_LEVELS.get(mode, MODE_LEVELS["normal"])

    def send(self, text: str, markup: dict | None = None, level: str = INFO) -> None:
        log.info("NOTIFY[%s]: %s", level, text)
        if not self.enabled or not self.allowed(level):
            return
        params = {
            "chat_id": self.chat_id,
            "text": f"hh-autoraise: {text}",
            "disable_web_page_preview": "true",
        }
        if markup:
            params["reply_markup"] = json.dumps(markup, ensure_ascii=False)
        try:
            self.api("sendMessage", params)
        except Exception as e:  # уведомления не должны ронять сервис
            log.warning("Не удалось отправить в Telegram: %s", e)

    def edit(self, message_id: int, text: str, markup: dict | None = None) -> None:
        params = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": f"hh-autoraise: {text}",
            "disable_web_page_preview": "true",
        }
        if markup:
            params["reply_markup"] = json.dumps(markup, ensure_ascii=False)
        try:
            self.api("editMessageText", params)
        except Exception as e:
            if "message is not modified" not in str(e):
                log.warning("Не удалось обновить сообщение в Telegram: %s", e)

    def send_document(self, filename: str, content: bytes, caption: str = "") -> bool:
        """Отправить файл (multipart/form-data, без сторонних библиотек)."""
        if not self.enabled:
            return False
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("chat_id", str(self.chat_id)), ("caption", caption)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                         f"{value}\r\n".encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                     f'filename="{filename}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'
                     .encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendDocument", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            if not body.get("ok"):
                raise RuntimeError(body)
            return True
        except Exception as e:
            log.warning("Не удалось отправить файл в Telegram: %s", e)
            return False

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self.api(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
            )
        except Exception as e:
            log.warning("Telegram answerCallbackQuery: %s", e)


class CommandListener(threading.Thread):
    """Фоновый long polling getUpdates: команды и нажатия кнопок только из чата TG_CHAT_ID.

    callbacks — обработчики inline-кнопок по префиксу callback_data («quiet:23-08» → «quiet»).
    awaiting — если задан, следующий текст (не команда) уйдёт в него (ввод своего значения).
    """

    def __init__(
        self,
        notifier: Notifier,
        handlers: dict[str, Handler],
        callbacks: dict[str, Callback] | None = None,
    ) -> None:
        super().__init__(name="tg-commands", daemon=True)
        self.notifier = notifier
        self.handlers = handlers
        self.callbacks = callbacks or {}
        self.awaiting: TextInput | None = None

    def run(self) -> None:
        offset = self._skip_old()
        while True:
            try:
                updates = self.notifier.api(
                    "getUpdates",
                    {
                        "offset": offset or 0,
                        "timeout": 50,
                        "allowed_updates": '["message", "callback_query"]',
                    },
                    timeout=60,
                )
            except Exception as e:
                log.warning("Не удалось получить команды из Telegram: %s", e)
                time.sleep(30)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        self.handle_callback(upd["callback_query"])
                    else:
                        self.handle(upd.get("message") or {})
                except Exception:
                    log.exception("Ошибка обработки команды из Telegram")

    def _skip_old(self) -> int | None:
        """Пропустить команды, пришедшие до запуска сервиса."""
        try:
            last = self.notifier.api("getUpdates", {"offset": -1, "timeout": 0})
        except Exception as e:
            log.warning("Telegram getUpdates: %s", e)
            return None
        return last[-1]["update_id"] + 1 if last else None

    def _own_chat(self, chat: dict | None) -> bool:
        chat_id = str((chat or {}).get("id", ""))
        if chat_id != str(self.notifier.chat_id):
            if chat_id:
                log.warning("Команда из чужого чата %s проигнорирована", chat_id)
            return False
        return True

    def _send(self, reply: str | Reply) -> None:
        if isinstance(reply, Reply):
            self.notifier.send(reply.text, reply.markup, level=REPLY)
        else:
            self.notifier.send(reply, level=REPLY)

    def handle(self, message: dict) -> None:
        if not self._own_chat(message.get("chat")):
            return
        text = (message.get("text") or "").strip()
        if not text:
            return
        if not text.startswith("/"):
            if self.awaiting is not None:
                awaiting, self.awaiting = self.awaiting, None
                self._send(awaiting(text))
            return
        self.awaiting = None  # любая команда отменяет ввод своего значения
        command = text.split()[0].split("@")[0].lower()
        handler = self.handlers.get(command)
        if handler is None:
            self._send(
                "Команды: "
                + ", ".join(self.handlers)
                + (" — подробнее /help" if "/help" in self.handlers else "")
            )
            return
        log.info("Команда из Telegram: %s", command)
        self._send(handler())

    def handle_callback(self, query: dict) -> None:
        message = query.get("message") or {}
        if not self._own_chat(message.get("chat")):
            self.notifier.answer_callback(query.get("id", ""))
            return
        data = query.get("data") or ""
        prefix, _, arg = data.partition(":")
        callback = self.callbacks.get(prefix)
        if callback is None:
            self.notifier.answer_callback(
                query["id"], "Кнопка устарела, откройте /menu"
            )
            return
        log.info("Кнопка в Telegram: %s", data)
        reply = callback(arg)
        if isinstance(reply, Reply):
            self.notifier.answer_callback(query["id"], reply.toast)
            self.notifier.edit(message["message_id"], reply.text, reply.markup)
        else:
            self.notifier.answer_callback(query["id"])
            self._send(reply)
