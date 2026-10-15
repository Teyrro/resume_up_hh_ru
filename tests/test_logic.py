import random
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from app.mail import extract_code, message_text
from app.notify import CommandListener, Notifier
from app.schedule import next_run, parse_available_at

TZ = ZoneInfo("Asia/Novosibirsk")
NOW = datetime(2026, 10, 15, 20, 30, tzinfo=TZ)


def test_parse_full_date():
    dt = parse_available_at("Поднять вручную можно 16 октября 2026 в 00:18", NOW)
    assert dt == datetime(2026, 10, 16, 0, 18, tzinfo=TZ)


def test_parse_today():
    assert parse_available_at("Можно сегодня в 23:30", NOW) == NOW.replace(hour=23, minute=30)


def test_parse_tomorrow():
    dt = parse_available_at("Поднять вручную можно завтра в 01:05", NOW)
    assert dt == (NOW + timedelta(days=1)).replace(hour=1, minute=5)


def test_parse_none():
    assert parse_available_at("Поднять автоматически", NOW) is None


def test_next_run_uses_available_time():
    avail = [NOW + timedelta(hours=3), NOW + timedelta(hours=4)]
    nxt = next_run(NOW, avail, 240, 3, 15, random.Random(1))
    assert NOW + timedelta(hours=3, minutes=3) <= nxt <= NOW + timedelta(hours=3, minutes=15)


def test_next_run_window_4_to_7_hours():
    avail = [NOW + timedelta(hours=4)]
    for seed in range(50):
        nxt = next_run(NOW, avail, 240, 5, 180, random.Random(seed))
        assert NOW + timedelta(hours=4) <= nxt <= NOW + timedelta(hours=7)


def test_next_run_already_available_short_delay():
    nxt = next_run(NOW, [NOW - timedelta(minutes=1)], 240, 5, 180, random.Random(1))
    assert nxt <= NOW + timedelta(minutes=15)


def test_next_run_default_interval():
    nxt = next_run(NOW, [], 240, 3, 15, random.Random(1))
    assert nxt >= NOW + timedelta(hours=4)


def test_next_run_never_too_soon():
    nxt = next_run(NOW, [NOW - timedelta(minutes=1)], 240, 0, 0, random.Random(1))
    assert nxt >= NOW + timedelta(minutes=10)


def test_extract_code_near_word():
    assert extract_code("Ваш код для входа на hh.ru: 4821. Никому не сообщайте") == "4821"


def test_extract_code_ignores_year_and_time():
    text = "15 октября 2026 в 20:31 Добро пожаловать 739104"
    assert extract_code(text) == "739104"


def test_message_text_html():
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Код подтверждения"
    msg.attach(MIMEText("<html><body><p>Код:&nbsp;<b>5530</b></p></body></html>", "html", "utf-8"))
    assert extract_code(message_text(msg)) == "5530"


class _FakeNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__("token", "111")
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _listener(calls: list[str]) -> tuple[CommandListener, _FakeNotifier]:
    notifier = _FakeNotifier()
    return CommandListener(notifier, {"/resume": lambda: calls.append("r") or "ok"}), notifier


def test_command_from_owner():
    calls: list[str] = []
    listener, notifier = _listener(calls)
    listener.handle({"chat": {"id": 111}, "text": "/resume@my_bot"})
    assert calls == ["r"]
    assert notifier.sent == ["ok"]


def test_command_from_stranger_ignored():
    calls: list[str] = []
    listener, notifier = _listener(calls)
    listener.handle({"chat": {"id": 222}, "text": "/resume"})
    assert calls == []
    assert notifier.sent == []


def test_unknown_command_lists_commands():
    calls: list[str] = []
    listener, notifier = _listener(calls)
    listener.handle({"chat": {"id": 111}, "text": "/start"})
    listener.handle({"chat": {"id": 111}, "text": "привет"})
    assert calls == []
    assert notifier.sent == ["Команды: /resume"]
