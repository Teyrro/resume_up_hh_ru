"""Точка входа: цикл автоподнятия и служебные команды.

  python -m app.main run      — постоянная работа (по умолчанию в Docker)
  python -m app.main once     — один проход: вход при необходимости + поднятие
  python -m app.main check    — проверить IMAP, Telegram и вход на hh
  python -m app.main resume   — снять стоп после капчи/ошибок входа (или /resume в боте)
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from . import config as config_mod
from .hh import CaptchaError, LoginError, is_logged_in, login, open_context, raise_resumes
from .mail import MailCodeReader
from .notify import CommandListener, Notifier
from .schedule import next_run

log = logging.getLogger("hh-autoraise")

TG_HELP = """Команды:
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
                                  "Повторю завтра.")
                    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0)
                    return tomorrow
                state.setdefault("logins", []).append(now.isoformat())
                state["logins"] = state["logins"][-20:]
                _save_state(cfg, state)
                log.info("Сессии нет — выполняю вход по коду из почты")
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
    wake = threading.Event()
    plan: dict = {"next": None, "running": False}

    def tg_resume() -> str:
        if not cfg.stop_flag.exists():
            return "Стоп-флага нет, сервис работает."
        reason = cfg.stop_flag.read_text(encoding="utf-8").strip()
        cfg.stop_flag.unlink(missing_ok=True)
        wake.set()
        return f"Стоп снят ({reason}). Запускаю проход."

    def tg_next() -> str:
        now = datetime.now(tz)
        state = _load_state(cfg)
        if cfg.stop_flag.exists():
            head = (f"⛔ Сервис остановлен ({cfg.stop_flag.read_text(encoding='utf-8').strip()}). "
                    "Плановых поднятий нет — нужен /resume.")
        elif plan["running"]:
            head = "Сейчас идёт проход."
        elif plan["next"]:
            head = (f"Следующее плановое поднятие: {_fmt_dt(plan['next'])} "
                    f"(через {_fmt_delta(plan['next'] - now)}).")
        else:
            head = "Следующий запуск ещё не запланирован."
        lines = [head]
        if state.get("available_at"):
            lines.append("hh.ru разрешит поднятие: "
                         + ", ".join(_fmt_dt(a) for a in state["available_at"]) + ".")
        lines.append(f"Последний проход: {_fmt_dt(state.get('last_run'))}, "
                     f"поднято: {state.get('last_clicked', 0)}.")
        lines.append(f"Последнее поднятие: {_fmt_dt(state.get('last_raised'))}.")
        return "\n".join(lines)

    if notifier.enabled:
        CommandListener(notifier, {"/next": tg_next, "/resume": tg_resume,
                                   "/help": lambda: TG_HELP}).start()
    notifier.send("Сервис запущен.")
    failures = 0
    while True:
        if cfg.stop_flag.exists():
            log.error("Сервис остановлен (%s). Разберитесь и выполните команду resume.",
                      cfg.stop_flag.read_text(encoding="utf-8").strip())
            wake.wait(3600)
            wake.clear()
            if not cfg.stop_flag.exists():
                failures = 0
            continue
        plan.update(next=None, running=True)
        try:
            nxt = run_once(cfg, notifier)
            failures = 0
        except CaptchaError as e:
            cfg.stop_flag.write_text(f"captcha: {e}", encoding="utf-8")
            notifier.send("hh.ru показал капчу. Автоподнятие остановлено, чтобы не получить "
                          "блокировку. Войдите на hh вручную с этого IP или подождите сутки, "
                          "затем отправьте боту /resume "
                          "(или: docker compose run --rm hh-autoraise resume)")
            continue
        except LoginError as e:
            failures += 1
            notifier.send(f"Ошибка входа: {e}. Отладка сохранена в data/debug.")
            if failures >= 3:
                cfg.stop_flag.write_text(f"login: {e}", encoding="utf-8")
                notifier.send("3 ошибки входа подряд — сервис остановлен. "
                              "Нужна проверка (см. data/debug), затем /resume.")
                continue
            nxt = datetime.now(tz) + timedelta(minutes=60 * failures)
        except Exception as e:
            failures += 1
            log.exception("Непредвиденная ошибка")
            notifier.send(f"Непредвиденная ошибка: {e}")
            nxt = datetime.now(tz) + timedelta(minutes=min(30 * failures, 240))

        wait = max(60.0, (nxt - datetime.now(tz)).total_seconds())
        plan.update(next=datetime.now(tz) + timedelta(seconds=wait), running=False)
        log.info("Следующий запуск: %s (через %.0f мин)", nxt.strftime("%d.%m %H:%M"), wait / 60)
        wake.wait(wait)
        wake.clear()


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
        notifier.send("Проверка связи: Telegram работает.")
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


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cmd = argv[1] if len(argv) > 1 else "run"
    required, optional = config_mod.missing_vars()
    if required:
        print(f"Переменные: не хватает обязательных — {', '.join(required)} (см. .env.example)")
        return 1
    if cmd == "check":
        print("Переменные: OK" + (f" (не заданы необязательные: {', '.join(optional)})"
                                   if optional else ""))
    cfg = config_mod.load()
    notifier = Notifier(cfg.tg_bot_token, cfg.tg_chat_id)
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
