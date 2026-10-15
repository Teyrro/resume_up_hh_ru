"""Проверка, что проект и .env содержат все нужные переменные.

В сообщениях об ошибках — только имена переменных, значения не выводятся.
"""

import re
import warnings
from pathlib import Path

import pytest

from app.config import OPTIONAL_VARS, REQUIRED_VARS, missing_vars

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"


def _env_keys(path: Path, with_commented: bool = False) -> dict[str, str]:
    """Ключи и значения из env-файла; with_commented — учитывать и «# KEY=...»."""
    pattern = r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=(.*)$"
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(pattern, line)
        if m and (with_commented or not m.group(1)):
            result[m.group(2)] = m.group(3).strip().strip("'\"")
    return result


def _config_vars() -> set[str]:
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    return set(re.findall(r'_(?:env|int)\(\s*"([A-Z][A-Z0-9_]*)"', source))


def test_missing_vars_reports_names():
    required, optional = missing_vars(
        {"HH_LOGIN_EMAIL": "a@b.c", "IMAP_PASSWORD": "  "}
    )
    assert required == ["HH_RESUME_IDS", "IMAP_PASSWORD"]
    assert optional == list(OPTIONAL_VARS)


def test_env_example_lists_all_config_vars():
    documented = set(_env_keys(EXAMPLE_FILE, with_commented=True))
    absent = sorted(_config_vars() - documented)
    assert not absent, (
        f"В .env.example нет переменных, которые читает app/config.py: {', '.join(absent)}"
    )


def test_required_vars_are_read_by_config():
    unknown = sorted(set(REQUIRED_VARS + OPTIONAL_VARS) - _config_vars())
    assert not unknown, (
        f"REQUIRED/OPTIONAL_VARS не читаются в app/config.py: {', '.join(unknown)}"
    )


def test_env_file_has_all_vars():
    if not ENV_FILE.exists():
        pytest.skip(".env нет (свежий клон/CI) — создайте: cp .env.example .env")
    required, optional = missing_vars(_env_keys(ENV_FILE))
    if optional:
        warnings.warn(
            f"В .env не заданы необязательные (Telegram): {', '.join(optional)}",
            stacklevel=1,
        )
    assert not required, (
        f"В .env не хватает обязательных переменных: {', '.join(required)}"
    )
