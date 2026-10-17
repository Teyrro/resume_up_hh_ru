#!/usr/bin/env bash
# Установка hh-autoraise одной командой (без git и сборки — используется готовый образ):
#   curl -fsSL https://raw.githubusercontent.com/Teyrro/resume_up_hh_ru/master/install.sh | bash
# Папка установки — ~/hh-autoraise (другая: HH_AUTORAISE_DIR=/opt/hh-autoraise перед bash).
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/Teyrro/resume_up_hh_ru/master"
DIR="${HH_AUTORAISE_DIR:-$HOME/hh-autoraise}"

command -v curl >/dev/null || { echo "Нужен curl: sudo apt install curl" >&2; exit 1; }
mkdir -p "$DIR"
cd "$DIR"
echo "Скачиваю hh-autoraise в $DIR…"
for f in docker-compose.yml .env.example setup.sh; do
    curl -fsSL "$REPO_RAW/$f" -o "$f"
done
chmod +x setup.sh

# Мастер настройки задаёт вопросы — читаем ответы с терминала, а не из curl.
exec ./setup.sh </dev/tty
