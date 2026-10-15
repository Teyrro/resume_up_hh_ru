"""Получение одноразового кода hh.ru из почты по IMAP."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

# Код в письме hh — 4–6 цифр. Ищем рядом со словом «код», иначе первое отдельное число.
_CODE_NEAR_WORD = re.compile(r"(?:код|code)[^0-9]{0,40}?(\d{4,6})\b", re.IGNORECASE | re.DOTALL)
_ANY_CODE = re.compile(r"(?<![\d#])(\d{4,6})(?![\d])")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", text)


def message_text(msg: Message) -> str:
    """Склеивает текст письма (plain + html без тегов) и тему."""
    parts: list[str] = [_decode(msg.get("Subject"))]
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="replace")
        except LookupError:
            body = payload.decode("utf-8", errors="replace")
        parts.append(_html_to_text(body) if ctype == "text/html" else body)
    return "\n".join(parts)


def extract_code(text: str) -> str | None:
    m = _CODE_NEAR_WORD.search(text)
    if m:
        return m.group(1)
    # Убираем годы и время, чтобы не принять их за код.
    cleaned = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", " ", cleaned)
    m = _ANY_CODE.search(cleaned)
    return m.group(1) if m else None


def _message_time(msg: Message) -> datetime | None:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class MailCodeReader:
    def __init__(self, host: str, port: int, user: str, password: str,
                 folder: str = "INBOX", sender_filter: str = "hh.ru") -> None:
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.folder = folder
        self.sender_filter = sender_filter

    def check_login(self) -> None:
        """Проверка, что IMAP-доступ работает (для команды check)."""
        with imaplib.IMAP4_SSL(self.host, self.port) as imap:
            imap.login(self.user, self.password)
            imap.select(self.folder, readonly=True)

    def _fetch_candidates(self, since: datetime) -> list[tuple[datetime, str]]:
        found: list[tuple[datetime, str]] = []
        with imaplib.IMAP4_SSL(self.host, self.port) as imap:
            imap.login(self.user, self.password)
            imap.select(self.folder, readonly=True)
            day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
            status, data = imap.search(None, f'(FROM "{self.sender_filter}" SINCE {day})')
            if status != "OK" or not data or not data[0]:
                return found
            ids = data[0].split()[-15:]  # последние письма достаточно
            for msg_id in reversed(ids):
                status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sent = _message_time(msg)
                if sent is None or sent < since:
                    continue
                code = extract_code(message_text(msg))
                if code:
                    found.append((sent, code))
        found.sort(key=lambda x: x[0], reverse=True)
        return found

    def wait_for_code(self, requested_at: datetime, timeout_s: int = 180,
                      poll_s: int = 7) -> str | None:
        """Ждёт письмо с кодом, пришедшее после requested_at (минус 60 с запаса)."""
        since = requested_at.astimezone(timezone.utc) - timedelta(seconds=60)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                candidates = self._fetch_candidates(since)
            except imaplib.IMAP4.error as e:
                raise RuntimeError(f"Ошибка IMAP: {e}") from e
            except OSError as e:
                log.warning("IMAP недоступен: %s", e)
                candidates = []
            if candidates:
                sent, code = candidates[0]
                log.info("Код найден в письме от %s", sent.isoformat())
                return code
            time.sleep(poll_s)
        return None
