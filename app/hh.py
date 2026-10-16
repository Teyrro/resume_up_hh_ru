"""Действия в браузере на hh.ru: проверка сессии, вход по коду из почты, поднятие резюме."""

from __future__ import annotations

import logging
import math
import random
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import BrowserContext, Locator, Page, Playwright
from playwright.sync_api import TimeoutError as PWTimeout

from .config import Config
from .mail import MailCodeReader
from .schedule import parse_available_at

log = logging.getLogger(__name__)

# Полный Chromium в новом headless-режиме: настоящие плагины, userAgentData и Client Hints.
BROWSER_CHANNEL = "chromium"
# Экран и окно как у развёрнутого браузера на типичном ноутбуке (1920x1080 при масштабе 125%).
SCREEN = {"width": 1536, "height": 864}
VIEWPORT = {"width": 1536, "height": 730}

_CAPTCHA_MARKERS = re.compile(
    r"captcha|капч|не робот|подтвердите, что вы человек|smart-?captcha", re.IGNORECASE
)
_RAISE_BUTTON = re.compile(
    r"^\s*Поднять(\s+(вручную|в поиске|резюме|сейчас))?\s*$", re.IGNORECASE
)
_AVAILABLE_TEXT = re.compile(
    r"(Поднять[^\n]{0,40}можно[^\n]{0,60}|Можно\s+(сегодня|завтра)[^\n]{0,20})",
    re.IGNORECASE,
)


class CaptchaError(RuntimeError):
    """hh показал капчу или иную проверку — автоматически не решаем."""


class LoginError(RuntimeError):
    pass


@dataclass
class RaiseResult:
    clicked: int
    available_at: list[datetime]
    details: list[str]


def _pause(a: float = 0.6, b: float = 1.8) -> None:
    time.sleep(random.uniform(a, b))


def browser_user_agent(executable: str) -> str:
    """User-agent настоящей версии Chromium, но без «HeadlessChrome».

    Версия берётся из самого браузера, поэтому UA всегда совпадает с движком,
    navigator.userAgentData и заголовками Sec-CH-UA (их Chromium формирует сам).
    """
    out = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
    if not m:
        raise RuntimeError(f"Не удалось узнать версию Chromium: {out!r}")
    return (
        f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{m[1]}.0.0.0 Safari/537.36"
    )


def open_context(pw: Playwright, cfg: Config) -> BrowserContext:
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(cfg.profile_dir),
        channel=BROWSER_CHANNEL,
        headless=cfg.headless,
        locale="ru-RU",
        timezone_id=cfg.timezone,
        user_agent=browser_user_agent(pw.chromium.executable_path),
        screen=SCREEN,
        viewport=VIEWPORT,
        args=["--disable-blink-features=AutomationControlled"],
    )


# --- «Человеческая» механика: мышь по кривой, прокрутка колесом, неровный набор. ---

_mouse_pos: dict[int, tuple[float, float]] = {}


def _move_mouse(page: Page, x: float, y: float) -> None:
    """Плавное движение по кривой Безье с неравномерной скоростью."""
    sx, sy = _mouse_pos.get(id(page)) or (
        random.uniform(0.2, 0.8) * VIEWPORT["width"],
        random.uniform(0.1, 0.5) * VIEWPORT["height"],
    )
    dist = math.hypot(x - sx, y - sy)
    # Контрольная точка в стороне от прямой — траектория получается дугой.
    cx = (sx + x) / 2 + random.uniform(-0.25, 0.25) * dist
    cy = (sy + y) / 2 + random.uniform(-0.25, 0.25) * dist
    steps = max(8, min(45, int(dist / random.uniform(12, 22))))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # разгон и торможение
        px = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * x
        py = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * y
        page.mouse.move(px, py)
        time.sleep(random.uniform(0.004, 0.018))
    _mouse_pos[id(page)] = (x, y)


def _scroll_to(page: Page, loc: Locator) -> dict | None:
    """Прокрутить колесом мыши, пока элемент не окажется в видимой части окна."""
    for _ in range(25):
        box = loc.bounding_box()
        if box is None:
            return None
        top, bottom = 90, VIEWPORT["height"] - 90
        if top <= box["y"] and box["y"] + box["height"] <= bottom:
            return box
        delta = box["y"] - VIEWPORT["height"] * random.uniform(0.35, 0.55)
        step = math.copysign(min(abs(delta), random.uniform(180, 420)), delta)
        page.mouse.wheel(0, step)
        time.sleep(random.uniform(0.12, 0.35))
    return loc.bounding_box()


def _human_click(page: Page, loc: Locator) -> None:
    """Довести мышь до случайной точки на элементе и нажать; иначе — обычный click()."""
    try:
        box = _scroll_to(page, loc)
        if box and box["width"] > 2 and box["height"] > 2:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            # Кликаем «по-человечески», только если в этой точке действительно наш элемент.
            if loc.evaluate(
                "(el, [x, y]) => el.contains(document.elementFromPoint(x, y))", [x, y]
            ):
                _move_mouse(page, x, y)
                time.sleep(random.uniform(0.08, 0.3))
                page.mouse.down()
                time.sleep(random.uniform(0.05, 0.14))
                page.mouse.up()
                return
    except Exception as e:
        log.debug("Человеческий клик не удался, обычный click(): %s", e)
    loc.click()


def _human_type(page: Page, text: str) -> None:
    """Набор с неравномерными паузами между символами и редкими «задумчивыми» остановками."""
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.06, 0.22))
        if random.random() < 0.08:
            time.sleep(random.uniform(0.3, 0.9))


def _look_around(page: Page) -> None:
    """Немного полистать страницу, как человек, который её читает."""
    for _ in range(random.randint(1, 3)):
        page.mouse.wheel(0, random.uniform(150, 450))
        time.sleep(random.uniform(0.4, 1.2))
    if random.random() < 0.6:
        page.mouse.wheel(0, -random.uniform(200, 600))
        time.sleep(random.uniform(0.3, 0.8))


def _open_my_resumes(page: Page, cfg: Config) -> None:
    """Иногда заходить через главную, как человек; иначе — сразу на «Мои резюме»."""
    url = f"{cfg.hh_base_url}/applicant/my_resumes"
    if random.random() < 0.4:
        page.goto(f"{cfg.hh_base_url}/", wait_until="domcontentloaded")
        _pause(1.5, 3.5)
        _look_around(page)
        link = page.locator("a[href*='/applicant/my_resumes']")
        visible = [
            link.nth(i) for i in range(min(link.count(), 5)) if link.nth(i).is_visible()
        ]
        if visible:
            _human_click(page, visible[0])
            try:
                page.wait_for_url("**/applicant/my_resumes**", timeout=15000)
                return
            except PWTimeout:
                pass
        page.goto(url, wait_until="domcontentloaded", referer=f"{cfg.hh_base_url}/")
        return
    page.goto(url, wait_until="domcontentloaded")


def dump_debug(page: Page, cfg: Config, tag: str) -> Path | None:
    """Сохраняет скриншот и HTML для разбора, если что-то пошло не так."""
    try:
        cfg.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = cfg.debug_dir / f"{stamp}-{tag}"
        page.screenshot(path=f"{base}.png", full_page=True)
        Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
        # Храним только последние 30 файлов.
        files = sorted(cfg.debug_dir.glob("*"), key=lambda p: p.stat().st_mtime)
        for old in files[:-30]:
            old.unlink(missing_ok=True)
        log.info("Отладочные файлы: %s.{png,html}", base)
        return base
    except Exception as e:
        log.warning("Не удалось сохранить отладку: %s", e)
        return None


def _check_captcha(page: Page) -> None:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except PWTimeout:
        return
    if _CAPTCHA_MARKERS.search(body) or page.locator("iframe[src*='captcha']").count():
        raise CaptchaError("hh.ru запросил капчу")


def _first_visible(
    page: Page, selectors: list[str], timeout_ms: int = 8000
) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 5)):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue
        time.sleep(0.4)
    return None


def _click_button_by_text(page: Page, names: list[str], timeout_ms: int = 6000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for name in names:
            for loc in (
                page.get_by_role("button", name=name, exact=True),
                page.locator(f"button:has-text('{name}')"),
            ):
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            _human_click(page, item)
                            return True
                    except Exception:
                        continue
        time.sleep(0.4)
    return False


def is_logged_in(page: Page, cfg: Config) -> bool:
    _open_my_resumes(page, cfg)
    _pause(1.5, 2.5)
    if "/account/login" in page.url or "/account/signup" in page.url:
        return False
    if page.locator(
        "[data-qa='account-login-form'], [data-qa='applicant-login-card']"
    ).count():
        return False
    return page.locator("text=Мои резюме").count() > 0


def login(page: Page, cfg: Config, mail: MailCodeReader) -> None:
    """Вход по почте: запрос кода → чтение письма по IMAP → ввод кода."""
    page.goto(
        f"{cfg.hh_base_url}/account/login?backurl=%2Fapplicant%2Fmy_resumes",
        wait_until="domcontentloaded",
    )
    _pause(1.5, 3)
    _check_captcha(page)

    # Шаг 0: выбор типа аккаунта «Я ищу работу». data-qa у радио составной
    # («account-type-card-APPLICANT checked»), сам input скрыт под карточкой.
    applicant_card = page.locator("[data-qa~='account-type-card-APPLICANT']")
    if applicant_card.count():
        applicant_card.first.check(force=True)
        _pause()
        submit = page.locator("[data-qa='submit-button']")
        if submit.count() and submit.first.is_visible():
            _human_click(page, submit.first)
            _pause(1.5, 2.5)

    # Шаг 1: вкладка «Почта» (по умолчанию открыт «Телефон»).
    email_tab = page.locator("[data-qa~='credential-type-email']")
    try:
        email_tab.first.check(force=True, timeout=8000)
    except PWTimeout:
        loc = page.get_by_text("Почта", exact=True)
        if loc.count():
            _human_click(page, loc.first)
    _pause()

    email_input = _first_visible(
        page,
        [
            "input[data-qa='applicant-login-input-email']",
            "input[type='email']",
            "input[inputmode='email']",
            "input[name='login']",
            "input[name*='email' i]",
            "input[data-qa*='email' i]",
            "input[autocomplete='email']",
            "input[autocomplete='username']",
        ],
    )
    if email_input is None:
        dump_debug(page, cfg, "no-email-input")
        raise LoginError("Не нашёл поле для почты на странице входа")
    _human_click(page, email_input)
    email_input.fill("")
    _human_type(page, cfg.hh_login_email)
    _pause()

    requested_at = datetime.now(ZoneInfo(cfg.timezone))
    submit = page.locator("[data-qa='submit-button']")
    if submit.count() and submit.first.is_visible():
        _human_click(page, submit.first)  # «Дальше»
    elif not _click_button_by_text(page, ["Дальше", "Получить код", "Продолжить"]):
        email_input.press("Enter")
    _pause(2, 3.5)
    _check_captcha(page)

    # Шаг 2: поле для кода.
    code_input = _first_visible(
        page,
        [
            "input[autocomplete='one-time-code']",
            "input[name*='code' i]",
            "input[data-qa*='code' i]",
            "input[inputmode='numeric']",
        ],
        timeout_ms=30000,
    )
    if code_input is None:
        dump_debug(page, cfg, "no-code-input")
        raise LoginError(
            "hh не показал поле для кода (возможно, изменился сценарий входа)"
        )

    log.info("Код запрошен, жду письмо (до %s с)…", cfg.code_wait_seconds)
    code = mail.wait_for_code(requested_at, timeout_s=cfg.code_wait_seconds)
    if not code:
        dump_debug(page, cfg, "no-code-email")
        raise LoginError("Письмо с кодом не пришло вовремя")

    _human_click(page, code_input)
    single_digit = page.locator(
        "input[maxlength='1'][inputmode='numeric'], input[maxlength='1']"
    )
    if single_digit.count() < len(code):
        code_input.fill("")
    _human_type(page, code)
    _pause(1, 2)
    _click_button_by_text(
        page, ["Войти", "Подтвердить", "Дальше", "Продолжить"], timeout_ms=3000
    )
    _pause(3, 5)
    _check_captcha(page)

    if not is_logged_in(page, cfg):
        dump_debug(page, cfg, "login-failed")
        raise LoginError("После ввода кода вход не выполнен")
    log.info("Вход выполнен")


def _touch_via_http(page: Page, cfg: Config, resume_id: str) -> bool:
    """Запасной путь: тот же запрос, который делает кнопка «Поднять»."""
    script = """async ([base, id]) => {
        const xsrf = (document.cookie.match(/(?:^|; )_xsrf=([^;]+)/) || [])[1] || '';
        const body = new URLSearchParams({resume: id, undirectable: 'true'});
        const r = await fetch(base + '/applicant/resumes/touch', {
            method: 'POST', body, credentials: 'include',
            headers: {'X-Xsrftoken': decodeURIComponent(xsrf), 'X-Requested-With': 'XMLHttpRequest'}
        });
        return r.status;
    }"""
    try:
        status = page.evaluate(script, [cfg.hh_base_url, resume_id])
        log.info("touch %s → HTTP %s", resume_id, status)
        return 200 <= int(status) < 300
    except Exception as e:
        log.warning("touch %s не удался: %s", resume_id, e)
        return False


def raise_resumes(page: Page, cfg: Config) -> RaiseResult:
    tz = ZoneInfo(cfg.timezone)
    # Обычно страница уже открыта проверкой сессии — человек не стал бы её перезагружать.
    if "/applicant/my_resumes" not in page.url:
        _open_my_resumes(page, cfg)
    _pause(2, 3.5)
    _check_captcha(page)
    _look_around(page)

    clicked = 0
    details: list[str] = []
    buttons = page.get_by_role("button", name=_RAISE_BUTTON)
    count = buttons.count()
    for i in range(count):
        btn = buttons.nth(i)
        try:
            # «Поднять автоматически» — платная услуга, не нажимать ни при каких условиях.
            if "resume-update-button_actions" in (btn.get_attribute("data-qa") or ""):
                continue
            if btn.is_visible() and btn.is_enabled():
                _pause()
                _human_click(page, btn)
                clicked += 1
                details.append(f"нажата кнопка «{btn.inner_text().strip()}»")
                _pause(2, 4)
        except Exception as e:
            log.warning("Не удалось нажать кнопку поднятия: %s", e)

    # Перечитываем страницу и собираем время следующего поднятия.
    page.reload(wait_until="domcontentloaded")
    _pause(2, 3)
    body = page.locator("body").inner_text()
    now = datetime.now(tz)
    available: list[datetime] = []
    for m in _AVAILABLE_TEXT.finditer(body):
        dt = parse_available_at(m.group(0), now)
        if dt:
            available.append(dt)
            details.append(m.group(0).strip())

    # Если кнопок не нашли, но hh пишет, что время уже наступило — пробуем запасной путь.
    if clicked == 0 and cfg.resume_ids and (not available or min(available) <= now):
        # Сохраняем страницу, чтобы по HTML поправить поиск кнопки (_RAISE_BUTTON).
        dump_debug(page, cfg, "raise-no-button")
        for rid in cfg.resume_ids:
            if _touch_via_http(page, cfg, rid):
                clicked += 1
                details.append(f"поднято через запрос: {rid}")
        if clicked:
            page.reload(wait_until="domcontentloaded")
            _pause(2, 3)
            body = page.locator("body").inner_text()
            available = [
                d
                for d in (
                    parse_available_at(m.group(0), now)
                    for m in _AVAILABLE_TEXT.finditer(body)
                )
                if d
            ]

    if clicked == 0 and not available:
        dump_debug(page, cfg, "raise-unknown")
    return RaiseResult(clicked=clicked, available_at=available, details=details)
