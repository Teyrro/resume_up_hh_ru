"""Разбор времени «Поднять вручную можно ...» и расчёт следующего запуска."""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, tzinfo

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

_FULL = re.compile(r"(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?\s+в\s+(\d{1,2}):(\d{2})", re.IGNORECASE)
_TODAY = re.compile(r"(сегодня|завтра)\s+в\s+(\d{1,2}):(\d{2})", re.IGNORECASE)
_TIME_ONLY = re.compile(r"\bв\s+(\d{1,2}):(\d{2})")


def parse_available_at(text: str, now: datetime) -> datetime | None:
    """Возвращает момент, когда hh разрешит поднять резюме, или None.

    Примеры: «Поднять вручную можно 16 октября 2026 в 00:18»,
    «Можно сегодня в 23:30», «Поднять в 23:30».
    """
    tz: tzinfo | None = now.tzinfo
    m = _FULL.search(text)
    if m and m.group(2).lower() in _MONTHS:
        day, month = int(m.group(1)), _MONTHS[m.group(2).lower()]
        year = int(m.group(3)) if m.group(3) else now.year
        dt = datetime(year, month, day, int(m.group(4)), int(m.group(5)), tzinfo=tz)
        if not m.group(3) and dt < now - timedelta(days=180):
            dt = dt.replace(year=year + 1)
        return dt
    m = _TODAY.search(text)
    if m:
        dt = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        if m.group(1).lower() == "завтра":
            dt += timedelta(days=1)
        return dt
    m = _TIME_ONLY.search(text)
    if m:
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if dt < now - timedelta(minutes=5):
            dt += timedelta(days=1)
        return dt
    return None


def next_run(now: datetime, available: list[datetime], min_interval_min: int,
             jitter_min: int, jitter_max: int, rng: random.Random | None = None) -> datetime:
    """Следующий запуск: ближайшее разрешённое время поднятия + случайная задержка.

    Задержка большая (по умолчанию до 3 ч), чтобы поднятия шли не ровно раз в 4 часа,
    а в случайный момент через 4–7 часов. Если поднять уже можно, но не вышло —
    задержка не больше 15 минут. Если время не удалось узнать — через min_interval_min.
    Никогда не раньше чем через 10 минут, чтобы не долбить сайт при сбоях.
    """
    rng = rng or random.Random()
    jitter = timedelta(minutes=rng.uniform(jitter_min, jitter_max))
    future = [a for a in available if a > now]
    if future:
        target = min(future)
    elif available:
        target = now  # уже можно — поднимем после небольшой паузы
        jitter = min(jitter, timedelta(minutes=15))
    else:
        target = now + timedelta(minutes=min_interval_min)
    return max(target + jitter, now + timedelta(minutes=10))
