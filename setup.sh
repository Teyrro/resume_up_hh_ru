#!/usr/bin/env bash
# Мастер первой настройки hh-autoraise.
# Спрашивает значения, создаёт файл переменных .env, проверяет настройки и запускает сервис.
# Запуск из папки проекта: ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${HH_SETUP_ENV_FILE:-.env}"  # переопределение — только для тестов

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

# ask "Вопрос" [значение по умолчанию] — ответ в stdout, подсказка в stderr.
ask() {
    local answer
    read -r -p "$1${2:+ [$2]}: " answer
    printf '%s' "${answer:-${2:-}}"
}
ask_secret() {
    local answer
    read -r -s -p "$1: " answer
    echo >&2
    printf '%s' "$answer"
}
yes_no() {  # yes_no "Вопрос" y|n — код возврата 0, если «да»
    local answer
    read -r -p "$1 [$([ "$2" = y ] && echo Y/n || echo y/N)]: " answer
    answer=${answer:-$2}
    [[ $answer =~ ^[YyДд] ]]
}

# --- 1. Docker ---------------------------------------------------------------
bold "1/6 Проверка Docker"
if ! command -v docker >/dev/null 2>&1; then
    warn "Docker не установлен."
    if [ "$(uname -s)" = Linux ] && yes_no "Установить Docker официальным скриптом get.docker.com?" y; then
        command -v curl >/dev/null || fail "Нужен curl: sudo apt install curl"
        curl -fsSL https://get.docker.com | sudo sh
    else
        fail "Установите Docker: https://docs.docker.com/engine/install/ и запустите ./setup.sh снова."
    fi
fi
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null || [ -t 0 ]; then
        DOCKER=(sudo docker)
        "${DOCKER[@]}" info >/dev/null 2>&1 || fail "Docker не запущен: sudo systemctl start docker"
        warn "Docker работает только через sudo — команды ниже выполняются с sudo."
    else
        fail "Нет доступа к Docker. Добавьте себя в группу docker: sudo usermod -aG docker \$USER (и перелогиньтесь)."
    fi
fi
"${DOCKER[@]}" compose version >/dev/null 2>&1 || fail "Нужен Docker Compose v2 (команда «docker compose»)."
ok "Docker и Docker Compose на месте"

# --- 2. Существующие настройки -------------------------------------------------
if [ -f "$ENV_FILE" ] && ! yes_no "Настройки уже есть ($ENV_FILE). Перезаписать?" n; then
    ok "Оставляю текущие настройки"
else
    # --- 3. hh.ru ----------------------------------------------------------------
    bold "2/6 Аккаунт hh.ru"
    echo "Почта, с которой вы входите на hh.ru как соискатель (вход по коду из письма)."
    while :; do
        HH_LOGIN_EMAIL=$(ask "Email для входа на hh.ru")
        [[ $HH_LOGIN_EMAIL == *@*.* ]] && break
        warn "Похоже, это не email. Попробуйте ещё раз."
    done

    echo
    echo "Ссылки на резюме, которые нужно поднимать. Откройте резюме на hh.ru и скопируйте"
    echo "адрес вида https://hh.ru/resume/8efe31c5ff0c... По одной ссылке; пустая строка — готово."
    RESUMES=()
    while :; do
        line=$(ask "Ссылка на резюме №$(( ${#RESUMES[@]} + 1 ))")
        [ -z "$line" ] && { [ ${#RESUMES[@]} -gt 0 ] && break; warn "Нужно хотя бы одно резюме."; continue; }
        hash=$(grep -oE '[0-9a-f]{20,}' <<<"$line" | head -n 1 || true)
        if [ -n "$hash" ]; then RESUMES+=("$hash"); ok "Резюме $hash"; else warn "Не нашёл hash резюме в «$line»."; fi
    done
    HH_RESUME_IDS=$(IFS=,; echo "${RESUMES[*]}")

    # --- 4. Почта ------------------------------------------------------------------
    bold "3/6 Почта для кодов входа (IMAP)"
    IMAP_USER=$(ask "Почта, куда приходят коды от hh.ru" "$HH_LOGIN_EMAIL")
    case ${IMAP_USER##*@} in
        gmail.com|googlemail.com) IMAP_HOST=imap.gmail.com ;;
        yandex.ru|ya.ru|yandex.com) IMAP_HOST=imap.yandex.ru ;;
        mail.ru|bk.ru|inbox.ru|list.ru) IMAP_HOST=imap.mail.ru ;;
        *) IMAP_HOST=$(ask "IMAP-сервер вашей почты (например imap.example.com)") ;;
    esac
    ok "IMAP-сервер: $IMAP_HOST"
    if [ "$IMAP_HOST" = imap.gmail.com ]; then
        echo "Нужен ПАРОЛЬ ПРИЛОЖЕНИЯ Google (16 букв), обычный пароль не подойдёт:"
        echo "  1) включите двухэтапную аутентификацию: https://myaccount.google.com/security"
        echo "  2) создайте пароль: https://myaccount.google.com/apppasswords"
    else
        echo "Нужен пароль для внешних приложений (IMAP) — создаётся в настройках безопасности почты."
    fi
    while :; do
        IMAP_PASSWORD=$(ask_secret "Пароль приложения (ввод скрыт)" | tr -d ' ')
        if [ "$IMAP_HOST" = imap.gmail.com ] && ! [[ $IMAP_PASSWORD =~ ^[a-zA-Z]{16}$ ]]; then
            warn "У Google пароль приложения — 16 латинских букв. Проверьте и введите ещё раз."
            continue
        fi
        [ -n "$IMAP_PASSWORD" ] && break
    done

    # --- 5. Telegram -----------------------------------------------------------------
    bold "4/6 Telegram-уведомления (необязательно)"
    echo "Бот будет писать о поднятиях и ошибках и примет команды /menu, /next, /resume."
    echo "Создайте бота у @BotFather (https://t.me/BotFather → /newbot) и вставьте токен."
    TG_BOT_TOKEN="" TG_CHAT_ID=""
    command -v curl >/dev/null || warn "Нет curl — шаг с Telegram пропущен (sudo apt install curl)."
    while command -v curl >/dev/null; do
        TG_BOT_TOKEN=$(ask "Токен бота (Enter — пропустить)")
        [ -z "$TG_BOT_TOKEN" ] && { warn "Без Telegram: уведомлений и команд не будет."; break; }
        me=$(curl -fsS --max-time 15 "https://api.telegram.org/bot$TG_BOT_TOKEN/getMe" 2>/dev/null || true)
        bot=$(grep -oE '"username":"[^"]+"' <<<"$me" | cut -d'"' -f4 || true)
        if [ -z "$bot" ]; then warn "Telegram не принял токен. Проверьте его."; continue; fi
        ok "Бот @$bot"
        echo "Откройте https://t.me/$bot, нажмите «Start» и отправьте боту любое сообщение."
        echo -n "Жду сообщение (до 2 минут)"
        for _ in $(seq 1 24); do
            upd=$(curl -fsS --max-time 10 "https://api.telegram.org/bot$TG_BOT_TOKEN/getUpdates" 2>/dev/null || true)
            TG_CHAT_ID=$(grep -oE '"chat":\{"id":-?[0-9]+' <<<"$upd" | tail -n 1 | grep -oE -- '-?[0-9]+$' || true)
            [ -n "$TG_CHAT_ID" ] && break
            echo -n "."; sleep 5
        done
        echo
        if [ -z "$TG_CHAT_ID" ]; then
            TG_CHAT_ID=$(ask "Не дождался. Введите chat_id вручную (Enter — без Telegram)")
            [ -z "$TG_CHAT_ID" ] && TG_BOT_TOKEN=""
        fi
        [ -n "$TG_CHAT_ID" ] && ok "chat_id: $TG_CHAT_ID"
        break
    done

    # --- 6. Часовой пояс ----------------------------------------------------------------
    bold "5/6 Часовой пояс"
    echo "Тот, в котором hh.ru показывает вам время («Поднять вручную можно … в 14:05»)."
    TZ_DEFAULT=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
    [[ -z $TZ_DEFAULT || $TZ_DEFAULT == Etc/UTC || $TZ_DEFAULT == UTC ]] && TZ_DEFAULT=Europe/Moscow
    while :; do
        TZ_VALUE=$(ask "Часовой пояс (Europe/Moscow, Asia/Novosibirsk, …)" "$TZ_DEFAULT")
        [ -e "/usr/share/zoneinfo/$TZ_VALUE" ] || [ ! -d /usr/share/zoneinfo ] && break
        warn "Не знаю пояс «$TZ_VALUE». Формат: Регион/Город, например Europe/Moscow."
    done

    umask 077
    cat >"$ENV_FILE" <<EOF
# Создано ./setup.sh $(date '+%d.%m.%Y %H:%M'). Описание переменных — в .env.example.
HH_LOGIN_EMAIL=$HH_LOGIN_EMAIL
HH_RESUME_IDS=$HH_RESUME_IDS
IMAP_HOST=$IMAP_HOST
IMAP_PORT=993
IMAP_USER=$IMAP_USER
IMAP_PASSWORD=$IMAP_PASSWORD
TG_BOT_TOKEN=$TG_BOT_TOKEN
TG_CHAT_ID=$TG_CHAT_ID
TZ=$TZ_VALUE
DATA_DIR=/data
EOF
    chmod 600 "$ENV_FILE"
    ok "Настройки сохранены в $ENV_FILE (доступ только вам)"
fi

# --- 7. Проверка и запуск --------------------------------------------------------------
bold "6/6 Проверка и запуск"
echo "Скачиваю образ…"
if ! "${DOCKER[@]}" compose pull --quiet 2>/dev/null; then
    [ -f Dockerfile ] || fail "Не удалось скачать образ, а исходников для сборки нет."
    warn "Готовый образ недоступен — собираю из исходников (несколько минут)…"
    "${DOCKER[@]}" compose build
fi
echo "Проверяю почту, Telegram и hh.ru…"
if ! "${DOCKER[@]}" compose run --rm hh-autoraise check; then
    fail "Проверка не прошла — см. сообщения выше, исправьте и запустите ./setup.sh снова."
fi
if yes_no "Всё готово. Запустить сервис?" y; then
    "${DOCKER[@]}" compose up -d
    ok "Сервис запущен. При первом проходе он войдёт на hh.ru по коду из почты."
    echo
    echo "Полезное:"
    echo "  логи:        ${DOCKER[*]} compose logs --tail 50"
    echo "  остановить:  ${DOCKER[*]} compose down"
    echo "  обновить:    ${DOCKER[*]} compose pull && ${DOCKER[*]} compose up -d"
    [ -n "${TG_BOT_TOKEN:-}" ] && echo "  в Telegram:  /menu, /next, /help"
fi
