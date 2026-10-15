"""Действия в браузере на hh.ru: проверка сессии, вход по коду из почты, поднятие резюме."""

from __future__ import annotations

import logging
import random
import re
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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

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


def open_context(pw: Playwright, cfg: Config) -> BrowserContext:
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(cfg.profile_dir),
        headless=cfg.headless,
        locale="ru-RU",
        timezone_id=cfg.timezone,
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 860},
        args=["--disable-blink-features=AutomationControlled"],
    )


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
                            item.click()
                            return True
                    except Exception:
                        continue
        time.sleep(0.4)
    return False


def is_logged_in(page: Page, cfg: Config) -> bool:
    page.goto(f"{cfg.hh_base_url}/applicant/my_resumes", wait_until="domcontentloaded")
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
            submit.first.click()
            _pause(1.5, 2.5)

    # Шаг 1: вкладка «Почта» (по умолчанию открыт «Телефон»).
    email_tab = page.locator("[data-qa~='credential-type-email']")
    try:
        email_tab.first.check(force=True, timeout=8000)
    except PWTimeout:
        loc = page.get_by_text("Почта", exact=True)
        if loc.count():
            loc.first.click()
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
    email_input.click()
    email_input.fill("")
    email_input.type(cfg.hh_login_email, delay=random.randint(40, 110))
    _pause()

    requested_at = datetime.now(ZoneInfo(cfg.timezone))
    submit = page.locator("[data-qa='submit-button']")
    if submit.count() and submit.first.is_visible():
        submit.first.click()  # «Дальше»
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

    code_input.click()
    single_digit = page.locator(
        "input[maxlength='1'][inputmode='numeric'], input[maxlength='1']"
    )
    if single_digit.count() >= len(code):
        page.keyboard.type(code, delay=random.randint(80, 160))
    else:
        code_input.fill("")
        code_input.type(code, delay=random.randint(80, 160))
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
    page.goto(f"{cfg.hh_base_url}/applicant/my_resumes", wait_until="domcontentloaded")
    _pause(2, 3.5)
    _check_captcha(page)

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
                btn.scroll_into_view_if_needed()
                _pause()
                btn.click()
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
