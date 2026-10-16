"""Точка входа: цикл автоподнятия и служебные команды.

  python -m app.main run      — постоянная работа (по умолчанию в Docker)
  python -m app.main once     — один проход: вход при необходимости + поднятие
  python -m app.main check    — проверить IMAP, Telegram и вход на hh
  python -m app.main resume   — снять стоп после капчи/ошибок входа (или /resume в боте)
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from . import config as config_mod
from .hh import CaptchaError, LoginError, is_logged_in, login, open_context, raise_resumes
from .mail import MailCodeReader
from . import settings as settings_mod
from .notify import ERROR, REPLY, STATUS, CommandListener, Notifier, Reply
from .schedule import next_run

log = logging.getLogger("hh-autoraise")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

QUIET_PRESETS = ("23-08", "22-09", "00-08")
TG_COMMANDS = [
    {"command": "menu", "description": "Меню: тихие часы, уведомления, журнал"},
    {"command": "next", "description": "Когда следующее поднятие"},
    {"command": "resume", "description": "Снять стоп после капчи/ошибок"},
    {"command": "help", "description": "Что значат сообщения бота"},
]

TG_HELP = """Команды:
/menu — меню: состояние, тихие часы, режим уведомлений, выгрузка журнала событий.
/next — когда следующее плановое поднятие, итог последнего прохода.
/resume — снять стоп и сразу запустить проход (нужна только после «⛔» ниже).
/help — это сообщение.

Что значат сообщения бота:

ℹ️ «Сервис запущен» — контейнер (пере)запустился и сразу делает проход. \
Ничего делать не нужно.

ℹ️ «Выполнен повторный вход на hh.ru по коду из почты» — сессия hh.ru истекла, \
сервис сам запросил код, нашёл его в письме и вошёл. Ничего делать не нужно.

✅ «Поднято резюме: N» — нажата кнопка «Поднять» у N резюме. \
Следующий раз — в случайный момент через 4–7 часов (hh.ru разрешает раз в 4 часа).

⚠️ «Ошибка входа: …» — не удалось войти: не нашлось поле на странице входа, \
письмо с кодом не пришло или код не подошёл. Сервис повторит сам через 1–2 часа. \
Если повторяется — проверьте почту (папка, спам) и data/debug.

⚠️ «Непредвиденная ошибка: …» — сбой, не связанный со входом (сеть, hh.ru \
не открылся, браузер упал). Повторит сам через 30 мин, дальше реже (до 4 ч).

⚠️ «Сессия слетела, но дневной лимит попыток входа исчерпан» — за сутки уже было \
MAX_LOGINS_PER_DAY входов по коду (по умолчанию 3). Чтобы не вызвать капчу \
и не засыпать почту кодами, сервис ждёт до 9:00 следующего дня. \
Ничего делать не нужно, но частые слёты сессии — повод заглянуть в логи.

⛔ «hh.ru показал капчу…» — hh.ru заподозрил бота. Сервис остановлен, \
чтобы не получить блокировку. Войдите на hh.ru вручную в обычном браузере \
или подождите несколько часов, затем /resume.

⛔ «3 ошибки входа подряд — сервис остановлен» — вход стабильно не работает \
(чаще всего hh.ru поменял страницу входа или не приходят письма). \
Посмотрите data/debug (скриншоты) и логи, устраните причину, затем /resume.

🔔 Уведомления (/menu → 🔔): «Все статусы» — ещё и итог каждого прохода с временем \
следующего запуска; «Важные события» (по умолчанию) — как описано выше; \
«Только ошибки» — только ⚠️ и ⛔. Ответы на команды приходят всегда.

📄 Журнал (/menu → 📄 Выгрузить события) — файл со всеми событиями сервиса.

🌙 Тихие часы (/menu → 🌙) — время суток, когда резюме не поднимаются (например, ночью). \
Запуск, выпавший на них, переносится на конец окна + до 45 мин. По умолчанию выключены. \
Задаются кнопками 23–08 / 22–09 / 00–08 или «Своё время», отключаются кнопкой «Выключить».

Ответы на /resume:
• «Стоп снят (…). Запускаю проход.» — стоп снят, проход начался сразу;
• «Стоп-флага нет, сервис работает.» — останавливать было нечего.\
"""


def _load_state(cfg) -> dict:
    try:
        return json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(cfg, state: dict) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _fmt_dt(value: datetime | str | None) -> str:
    if not value:
        return "—"
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.strftime("%d.%m %H:%M")


def _fmt_delta(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def _status_text(cfg, passed: bool, target: datetime, now: datetime) -> str:
    """Итог прохода для режима уведомлений «Все статусы»."""
    when = f"{_fmt_dt(target)} (через {_fmt_delta(target - now)})"
    if not passed:
        return f"Следующая попытка: {when}."
    state = _load_state(cfg)
    text = f"Проход завершён: поднято {state.get('last_clicked', 0)}."
    if state.get("available_at"):
        text += " hh.ru разрешит: " + ", ".join(_fmt_dt(a) for a in state["available_at"]) + "."
    return f"{text} Следующий запуск: {when}."


def _logins_today(state: dict, now: datetime) -> int:
    day = now.date().isoformat()
    return sum(1 for t in state.get("logins", []) if t.startswith(day))


def run_once(cfg, notifier: Notifier) -> datetime:
    """Один проход. Возвращает время следующего запуска."""
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    state = _load_state(cfg)
    mail = MailCodeReader(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password,
                          cfg.imap_folder, cfg.mail_sender_filter)

    with sync_playwright() as pw:
        ctx = open_context(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not is_logged_in(page, cfg):
                if _logins_today(state, now) >= cfg.max_logins_per_day:
                    notifier.send("Сессия слетела, но дневной лимит попыток входа исчерпан. "
                                  "Повторю завтра.", level=ERROR)
                    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0)
                    return tomorrow
                state.setdefault("logins", []).append(now.isoformat())
                state["logins"] = state["logins"][-20:]
                _save_state(cfg, state)
                notifier.send("Сессии нет — выполняю вход по коду из почты.", level=STATUS)
                login(page, cfg, mail)
                notifier.send("Выполнен повторный вход на hh.ru по коду из почты.")

            result = raise_resumes(page, cfg)
            now = datetime.now(tz)
            if result.clicked:
                notifier.send(f"Поднято резюме: {result.clicked}.")
            log.info("Итог: поднято=%s; %s", result.clicked, "; ".join(result.details) or "—")
            state["last_run"] = now.isoformat()
            state["last_clicked"] = result.clicked
            state["available_at"] = [d.isoformat() for d in sorted(set(result.available_at))]
            if result.clicked:
                state["last_raised"] = now.isoformat()
            _save_state(cfg, state)
            return next_run(now, result.available_at, cfg.min_interval_minutes,
                            cfg.jitter_min_minutes, cfg.jitter_max_minutes)
        finally:
            ctx.close()


def cmd_run(cfg, notifier: Notifier) -> None:
    tz = ZoneInfo(cfg.timezone)
    wake = threading.Event()    # прервать ожидание: /resume или смена настроек
    replan = threading.Event()  # только пересчитать время (тихие часы), без прохода
    plan: dict = {"next": None, "base": None, "running": False}
    listener: CommandListener | None = None

    def quiet() -> settings_mod.Quiet | None:
        return settings_mod.get_quiet(cfg.settings_file)

    def tg_resume() -> str:
        if not cfg.stop_flag.exists():
            return "Стоп-флага нет, сервис работает."
        reason = cfg.stop_flag.read_text(encoding="utf-8").strip()
        cfg.stop_flag.unlink(missing_ok=True)
        wake.set()
        tail = " (сейчас тихие часы — проход будет после них)" if settings_mod.in_quiet(
            datetime.now(tz), quiet()) else ""
        return f"Стоп снят ({reason}). Запускаю проход.{tail}"

    def next_line() -> str:
        now = datetime.now(tz)
        if cfg.stop_flag.exists():
            return (f"⛔ Сервис остановлен ({cfg.stop_flag.read_text(encoding='utf-8').strip()}). "
                    "Плановых поднятий нет — нужен /resume.")
        if plan["running"]:
            return "Сейчас идёт проход."
        if plan["next"]:
            return (f"Следующее плановое поднятие: {_fmt_dt(plan['next'])} "
                    f"(через {_fmt_delta(plan['next'] - now)}).")
        return "Следующий запуск ещё не запланирован."

    def quiet_line() -> str:
        q = quiet()
        if q:
            return f"🌙 Тихие часы: {settings_mod.format_quiet(q)} — в это время резюме не поднимаются."
        return "🌙 Тихие часы: выключены — поднятия в любое время суток."

    def tg_next() -> str:
        state = _load_state(cfg)
        lines = [next_line()]
        if state.get("available_at"):
            lines.append("hh.ru разрешит поднятие: "
                         + ", ".join(_fmt_dt(a) for a in state["available_at"]) + ".")
        lines.append(f"Последний проход: {_fmt_dt(state.get('last_run'))}, "
                     f"поднято: {state.get('last_clicked', 0)}.")
        lines.append(f"Последнее поднятие: {_fmt_dt(state.get('last_raised'))}.")
        if quiet():
            lines.append(quiet_line())
        return "\n".join(lines)

    def notify_mode() -> str:
        return settings_mod.get_notify_mode(cfg.settings_file)

    back = [{"text": "« Назад", "callback_data": "menu:main"}]

    def main_menu(toast: str = "", note: str = "") -> Reply:
        markup = {"inline_keyboard": [
            [{"text": "🌙 Тихие часы", "callback_data": "menu:quiet"},
             {"text": "🔔 Уведомления", "callback_data": "menu:notify"}],
            [{"text": "📄 Выгрузить события", "callback_data": "menu:export"},
             {"text": "🔄 Обновить", "callback_data": "menu:refresh"}],
        ]}
        text = (f"⚙️ Меню\n\n{next_line()}\n{quiet_line()}\n"
                f"🔔 Уведомления: {settings_mod.NOTIFY_MODES[notify_mode()]}")
        if note:
            text += f"\n\n{note}"
        return Reply(text, markup, toast)

    def quiet_menu(toast: str = "", note: str = "") -> Reply:
        q = quiet()
        presets = [
            {"text": ("✅ " if settings_mod.parse_quiet(p) == q else "") + p.replace("-", "–"),
             "callback_data": f"quiet:{p}"}
            for p in QUIET_PRESETS
        ]
        markup = {"inline_keyboard": [
            presets,
            [{"text": "✏️ Своё время", "callback_data": "quiet:custom"},
             {"text": "🚫 Выключить", "callback_data": "quiet:off"}],
            back,
        ]}
        text = (f"🌙 Тихие часы\n\n{quiet_line()}\n{next_line()}\n\n"
                "Запуск, выпавший на тихие часы, переносится на конец окна + до 45 мин.")
        if note:
            text += f"\n\n{note}"
        return Reply(text, markup, toast)

    def notify_menu(toast: str = "") -> Reply:
        mode = notify_mode()
        markup = {"inline_keyboard": [
            *[[{"text": ("✅ " if key == mode else "") + label, "callback_data": f"notify:{key}"}]
              for key, label in settings_mod.NOTIFY_MODES.items()],
            back,
        ]}
        text = ("🔔 Уведомления\n\n"
                "• Все статусы — итог каждого прохода и время следующего запуска, "
                "а также всё из «Важных событий».\n"
                "• Важные события — запуск сервиса, вход по коду, поднятия и ошибки (по умолчанию).\n"
                "• Только ошибки — капча, ошибки входа, остановка сервиса, сбои.\n\n"
                "Ответы на команды и кнопки приходят всегда; в журнал (📄) пишется всё.")
        return Reply(text, markup, toast)

    def export_events() -> bool:
        files = [cfg.events_file.with_name(f"{cfg.events_file.name}.{i}") for i in (3, 2, 1)]
        data = b"".join(f.read_bytes() for f in [*files, cfg.events_file] if f.exists())
        if not data:
            return False
        name = f"hh-autoraise-events-{datetime.now(tz):%Y%m%d-%H%M}.log"
        return notifier.send_document(name, data, caption="Журнал событий hh-autoraise")

    def change_quiet(q: settings_mod.Quiet | None) -> None:
        settings_mod.set_quiet(cfg.settings_file, q)
        log.info("Тихие часы: %s", settings_mod.format_quiet(q) if q else "выключены")
        replan.set()
        wake.set()
        # Ответ с меню строится после пересчёта плана основным циклом.
        for _ in range(20):
            if not replan.is_set():
                break
            time.sleep(0.1)

    def custom_quiet(text: str) -> str | Reply:
        q = settings_mod.parse_quiet(text)
        if q is None:
            if listener is not None:
                listener.awaiting = custom_quiet
            return "Не понял формат. Пример: 23-08 или 23:30-07:45. Отмена — /menu."
        change_quiet(q)
        return quiet_menu(note=f"✅ Тихие часы: {settings_mod.format_quiet(q)}")

    def cb_quiet(arg: str) -> Reply:
        if arg == "custom":
            if listener is not None:
                listener.awaiting = custom_quiet
            return quiet_menu("Жду время сообщением",
                              "✏️ Отправьте время сообщением: 23-08 или 23:30-07:45.")
        if arg == "off":
            if quiet() is None:
                return quiet_menu("Тихие часы уже выключены")
            change_quiet(None)
            return quiet_menu("Тихие часы выключены")
        q = settings_mod.parse_quiet(arg)
        if q is None:
            return quiet_menu("Неизвестное значение")
        change_quiet(q)
        return quiet_menu(f"Тихие часы: {settings_mod.format_quiet(q)}")

    def cb_notify(arg: str) -> Reply:
        if arg not in settings_mod.NOTIFY_MODES:
            return notify_menu("Неизвестный режим")
        settings_mod.set_notify_mode(cfg.settings_file, arg)
        log.info("Режим уведомлений: %s", settings_mod.NOTIFY_MODES[arg])
        return notify_menu(f"Уведомления: {settings_mod.NOTIFY_MODES[arg]}")

    def cb_menu(arg: str) -> Reply:
        if arg == "quiet":
            return quiet_menu()
        if arg == "notify":
            return notify_menu()
        if arg == "export":
            if export_events():
                return main_menu("Журнал отправлен файлом")
            return main_menu("Не удалось отправить журнал", "⚠️ Журнал пуст или не отправился.")
        return main_menu("Обновлено" if arg == "refresh" else "")

    if notifier.enabled:
        listener = CommandListener(
            notifier,
            {"/menu": main_menu, "/next": tg_next, "/resume": tg_resume,
             "/help": lambda: TG_HELP},
            {"menu": cb_menu, "quiet": cb_quiet, "notify": cb_notify},
        )
        listener.start()
        try:
            notifier.api("setMyCommands", {"commands": json.dumps(TG_COMMANDS, ensure_ascii=False)})
        except Exception as e:
            log.warning("Telegram setMyCommands: %s", e)
    notifier.send("Сервис запущен.")
    failures = 0
    while True:
        if cfg.stop_flag.exists():
            plan.update(next=None, running=False)
            log.error("Сервис остановлен (%s). Разберитесь и выполните команду resume.",
                      cfg.stop_flag.read_text(encoding="utf-8").strip())
            wake.wait(3600)
            wake.clear()
            replan.clear()
            if not cfg.stop_flag.exists():
                failures = 0
            continue

        passed = False
        if settings_mod.in_quiet(datetime.now(tz), quiet()):
            log.info("Тихие часы — проход отложен")
            nxt = datetime.now(tz)
        else:
            plan.update(next=None, running=True)
            try:
                nxt = run_once(cfg, notifier)
                failures = 0
                passed = True
            except CaptchaError as e:
                cfg.stop_flag.write_text(f"captcha: {e}", encoding="utf-8")
                notifier.send("hh.ru показал капчу. Автоподнятие остановлено, чтобы не получить "
                              "блокировку. Войдите на hh вручную с этого IP или подождите сутки, "
                              "затем отправьте боту /resume "
                              "(или: docker compose run --rm hh-autoraise resume)", level=ERROR)
                continue
            except LoginError as e:
                failures += 1
                notifier.send(f"Ошибка входа: {e}. Отладка сохранена в data/debug.", level=ERROR)
                if failures >= 3:
                    cfg.stop_flag.write_text(f"login: {e}", encoding="utf-8")
                    notifier.send("3 ошибки входа подряд — сервис остановлен. "
                                  "Нужна проверка (см. data/debug), затем /resume.", level=ERROR)
                    continue
                nxt = datetime.now(tz) + timedelta(minutes=60 * failures)
            except Exception as e:
                failures += 1
                log.exception("Непредвиденная ошибка")
                notifier.send(f"Непредвиденная ошибка: {e}", level=ERROR)
                nxt = datetime.now(tz) + timedelta(minutes=min(30 * failures, 240))

        # Ожидание до следующего запуска; смена тихих часов пересчитывает время без прохода.
        announce = True
        while True:
            # Сигналы, пришедшие во время прохода, уже учтены: настройки читаются заново.
            replan.clear()
            wake.clear()
            target = settings_mod.apply_quiet(nxt, quiet())
            wait = max(60.0, (target - datetime.now(tz)).total_seconds())
            target = datetime.now(tz) + timedelta(seconds=wait)
            plan.update(next=target, running=False)
            log.info("Следующий запуск: %s (через %.0f мин)", target.strftime("%d.%m %H:%M"),
                     wait / 60)
            if announce:
                announce = False
                notifier.send(_status_text(cfg, passed, target, datetime.now(tz)), level=STATUS)
            wake.wait(wait)
            if replan.is_set():
                continue
            break


def cmd_check(cfg, notifier: Notifier) -> int:
    ok = True
    try:
        MailCodeReader(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password,
                       cfg.imap_folder).check_login()
        print("IMAP: OK")
    except Exception as e:
        ok = False
        print(f"IMAP: ОШИБКА — {e}")
    if notifier.enabled:
        notifier.send("Проверка связи: Telegram работает.", level=REPLY)
        print("Telegram: отправлено тестовое сообщение")
    else:
        print("Telegram: не настроен (необязательно)")
    with sync_playwright() as pw:
        ctx = open_context(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            print("hh.ru: сессия активна" if is_logged_in(page, cfg)
                  else "hh.ru: сессии нет — вход выполнится при первом запуске")
        finally:
            ctx.close()
    return 0 if ok else 1


def _setup_events_log(cfg) -> None:
    """Журнал всех событий в data/events.log (с ротацией) — выгружается кнопкой в /menu."""
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(cfg.events_file, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
    except OSError as e:
        log.warning("Журнал событий недоступен: %s", e)
        return
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    cmd = argv[1] if len(argv) > 1 else "run"
    required, optional = config_mod.missing_vars()
    if required:
        print(f"Переменные: не хватает обязательных — {', '.join(required)} (см. .env.example)")
        return 1
    if cmd == "check":
        print("Переменные: OK" + (f" (не заданы необязательные: {', '.join(optional)})"
                                   if optional else ""))
    cfg = config_mod.load()
    _setup_events_log(cfg)
    notifier = Notifier(cfg.tg_bot_token, cfg.tg_chat_id,
                        mode=lambda: settings_mod.get_notify_mode(cfg.settings_file))
    if cmd == "run":
        cmd_run(cfg, notifier)
        return 0
    if cmd == "once":
        nxt = run_once(cfg, notifier)
        print(f"Следующий рекомендуемый запуск: {nxt.isoformat()}")
        return 0
    if cmd == "check":
        return cmd_check(cfg, notifier)
    if cmd == "resume":
        cfg.stop_flag.unlink(missing_ok=True)
        print("Стоп снят. Перезапустите контейнер: docker compose restart")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
