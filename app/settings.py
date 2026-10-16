"""Настройки, которые меняются из Telegram (/menu), и тихие часы.

Хранятся в data/settings.json, чтобы переживать перезапуск контейнера.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Тихие часы: (начало, конец) в минутах от полуночи; окно может переходить через полночь.
Quiet = tuple[int, int]

_QUIET_RE = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?\s*$"
)
_lock = threading.Lock()


def parse_quiet(text: str) -> Quiet | None:
    """«23-08», «23:30-7:45», «23–08» → (1380, 480). None, если формат неверный."""
    m = _QUIET_RE.match(text or "")
    if not m:
        return None
    h1, m1, h2, m2 = int(m[1]), int(m[2] or 0), int(m[3]), int(m[4] or 0)
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    return None if start == end else (start, end)


def format_quiet(quiet: Quiet) -> str:
    (h1, m1), (h2, m2) = divmod(quiet[0], 60), divmod(quiet[1], 60)
    return f"{h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}"


def in_quiet(dt: datetime, quiet: Quiet | None) -> bool:
    if not quiet:
        return False
    start, end = quiet
    minute = dt.hour * 60 + dt.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def apply_quiet(
    dt: datetime, quiet: Quiet | None, rng: random.Random | None = None
) -> datetime:
    """Если dt попадает в тихие часы — перенести на конец окна + случайно 0–45 мин."""
    if not in_quiet(dt, quiet):
        return dt
    rng = rng or random.Random()
    end = dt.replace(hour=quiet[1] // 60, minute=quiet[1] % 60, second=0, microsecond=0)
    if end <= dt:
        end += timedelta(days=1)
    return end + timedelta(minutes=rng.uniform(0, 45))


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", path, e)
        return {}


def get_quiet(path: Path) -> Quiet | None:
    """Тихие часы из настроек; по умолчанию — выключены."""
    raw = load(path).get("quiet_hours")
    return parse_quiet(raw) if raw else None


def set_quiet(path: Path, quiet: Quiet | None) -> None:
    with _lock:
        data = load(path)
        data["quiet_hours"] = format_quiet(quiet).replace("–", "-") if quiet else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
