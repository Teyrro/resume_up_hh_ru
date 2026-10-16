import random
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from app.mail import extract_code, message_text
from app.main import _fmt_delta, _fmt_dt
from app.notify import CommandListener, Notifier, Reply
from app.settings import (apply_quiet, format_quiet, get_notify_mode, get_quiet, in_quiet,
                          parse_quiet, set_notify_mode, set_quiet)
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
        self.edited: list[tuple[int, str, dict | None]] = []
        self.answered: list[str] = []

    def send(self, text: str, markup: dict | None = None, level: str = "info") -> None:
        self.sent.append(text)

    def edit(self, message_id: int, text: str, markup: dict | None = None) -> None:
        self.edited.append((message_id, text, markup))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.answered.append(text)


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


def test_fmt_next_run():
    assert _fmt_dt(NOW) == "15.10 20:30"
    assert _fmt_dt(NOW.isoformat()) == "15.10 20:30"
    assert _fmt_dt(None) == "—"
    assert _fmt_delta(timedelta(hours=4, minutes=55, seconds=30)) == "4 ч 55 мин"
    assert _fmt_delta(timedelta(minutes=7)) == "7 мин"


def test_parse_quiet():
    assert parse_quiet("23-08") == (23 * 60, 8 * 60)
    assert parse_quiet(" 23:30 – 7:45 ") == (23 * 60 + 30, 7 * 60 + 45)
    assert parse_quiet("8-8") is None
    assert parse_quiet("25-08") is None
    assert parse_quiet("ночью") is None
    assert format_quiet((23 * 60, 8 * 60)) == "23:00–08:00"


def test_in_quiet_over_midnight():
    q = (23 * 60, 8 * 60)
    assert in_quiet(NOW.replace(hour=23, minute=10), q)
    assert in_quiet(NOW.replace(hour=3), q)
    assert not in_quiet(NOW.replace(hour=8, minute=0), q)
    assert not in_quiet(NOW.replace(hour=20), q)
    assert not in_quiet(NOW.replace(hour=3), None)


def test_apply_quiet_moves_to_morning():
    q = (23 * 60, 8 * 60)
    night = NOW.replace(hour=23, minute=40)
    moved = apply_quiet(night, q, random.Random(1))
    morning = (night + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    assert morning <= moved <= morning + timedelta(minutes=45)
    assert apply_quiet(NOW, q) == NOW  # 20:30 — не тихие часы


def test_quiet_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    assert get_quiet(path) is None  # по умолчанию выключены
    set_quiet(path, (22 * 60, 9 * 60))
    assert get_quiet(path) == (22 * 60, 9 * 60)
    set_quiet(path, None)
    assert get_quiet(path) is None


def test_callback_edits_menu_and_awaits_text():
    notifier = _FakeNotifier()
    got: list[str] = []
    listener = CommandListener(notifier, {"/menu": lambda: "menu"},
                               {"quiet": lambda arg: Reply(f"menu {arg}", {"k": 1}, "ok")})
    listener.handle_callback({"id": "1", "data": "quiet:23-08",
                              "message": {"message_id": 7, "chat": {"id": 111}}})
    assert notifier.edited == [(7, "menu 23-08", {"k": 1})]
    assert notifier.answered == ["ok"]

    listener.awaiting = lambda text: got.append(text) or "saved"
    listener.handle({"chat": {"id": 111}, "text": "22-09"})
    assert got == ["22-09"] and notifier.sent == ["saved"]
    assert listener.awaiting is None


def test_callback_from_stranger_ignored():
    notifier = _FakeNotifier()
    listener = CommandListener(notifier, {}, {"quiet": lambda arg: Reply("x")})
    listener.handle_callback({"id": "1", "data": "quiet:off",
                              "message": {"message_id": 7, "chat": {"id": 222}}})
    assert notifier.edited == []


def test_notify_modes_filter_levels(tmp_path):
    path = tmp_path / "settings.json"
    assert get_notify_mode(path) == "normal"  # по умолчанию
    notifier = Notifier("token", "111", mode=lambda: get_notify_mode(path))
    assert notifier.allowed("info") and notifier.allowed("error") and not notifier.allowed("status")
    set_notify_mode(path, "all")
    assert notifier.allowed("status")
    set_notify_mode(path, "errors")
    assert notifier.allowed("error") and notifier.allowed("reply")
    assert not notifier.allowed("info") and not notifier.allowed("status")


def test_settings_keep_each_other(tmp_path):
    path = tmp_path / "settings.json"
    set_quiet(path, (23 * 60, 8 * 60))
    set_notify_mode(path, "errors")
    assert get_quiet(path) == (23 * 60, 8 * 60)
    assert get_notify_mode(path) == "errors"
