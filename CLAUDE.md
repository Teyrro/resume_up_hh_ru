# hh-autoraise — контекст для продолжения работы

## Цель
Автономный сервис на собственном сервере: поднимает резюме на hh.ru в случайный момент
через 4–7 часов после прошлого поднятия (hh разрешает раз в 4 часа).
Если сессия hh слетела — сам входит по почте: запрашивает код, читает письмо из Gmail
по IMAP, вводит код. Затем нажимает «Поднять» в обоих резюме.
Без LLM/ML: чистый RPA (Playwright-селекторы + регулярки).

## Почему не через API
dev.hh.ru: «поддержка API для соискателей прекращена 15 декабря 2025» — токен соискателя
получить нельзя, поэтому браузерная автоматизация (Playwright).

## Данные
- Аккаунт hh: соискатель, почта для входа и кодов — <email, см. CLAUDE.local.md>
- Резюме (hash):
  - Python Backend-разработчик — `<hash резюме 1>`
  - Support L2 / Application Support — `<hash резюме 2>`
- Часовой пояс: Asia/Novosibirsk. Лимит hh: ручное поднятие не чаще раза в 4 часа.
- GitHub: `https://github.com/Teyrro/resume_up_hh_ru` (remote `origin`, ветка `master`).
  Пуш из WSL пока не работает: `gh` установлен в `~/.local/bin`, но пользователь не вошёл
  (`gh auth login`), SSH-ключ к GitHub не привязан. На GitHub только первый коммит —
  остальные доставлялись на сервер через `git bundle`. Репозиторий читается без
  авторизации (вероятно, публичный), а здесь email и hash резюме — уточнить у пользователя.
- Коммиты — от `<email, см. CLAUDE.local.md>` (локальный `user.email` репозитория;
  глобальный git config рабочий, не трогать).

## Сервер
- `ssh vpn` (<адрес сервера>, root), 2 ГБ RAM, 30 ГБ диск. SSH-конфиг в WSL у пользователя.
- 3x-ui работает НЕ в Docker, а как systemd: `x-ui.service`, `xray.service`
  (порты 443, 2053, 2096). ufw активен (deny incoming), fail2ban на SSH.
  **Не трогать x-ui/xray, ufw/iptables/nftables, сеть хоста.**
- Docker-том `marzban_postgres_data` — чужие данные (Marzban). Docker считает его
  неиспользуемым — **не удалять, не делать `docker system prune --volumes`**.
- Старая попытка `/opt/hh-agent` (OpenClaw + ChatGPT, сервис `hh-browser`, пользователь
  `hhagent`, npm `openclaw`) удалена 16.10.2026 по просьбе пользователя.
- Проект: `/opt/hh-autoraise` (git clone + bundle), образ собран (~924 МБ).
  **Сервис на сервере ещё НЕ запущен**: ждём, пока пользователь сам скопирует `.env`
  (`scp .env vpn:/opt/hh-autoraise/.env`, `chmod 600`). После этого: остановить локальный
  контейнер (`docker compose down` в WSL — два экземпляра = двойное поднятие и
  Telegram 409), на сервере `docker compose run --rm hh-autoraise check`, затем `up -d`.
- Пока сервис работает локально в WSL (`docker compose up -d`, данные в `./data`).
- Хук `protect-secrets` блокирует любые команды с `.env` (и локально, и через ssh) —
  `.env` не читать, просить пользователя.

## Что известно о вёрстке hh (проверено вживую)
- Страница `https://hh.ru/applicant/my_resumes`.
- Текст до разрешения: «Поднять вручную можно 16 октября 2026 в 00:18» /
  «… сегодня в 04:24» (`data-qa="title-description"`).
- Кнопка `data-qa="resume-update-button_actions"` с текстом «Поднять автоматически» — это
  ПЛАТНОЕ автоподнятие, её не нажимать.
- Вход по почте с кодом (селекторы в `login()`) — работает, проверено 15.10.2026.
- Ручное поднятие кнопкой — работает (16.10.2026 ~00:24 подняты оба резюме, подтвердил
  пользователь); точный текст кнопки в логах не сохранился.
- Запасной путь поднятия: `POST /applicant/resumes/touch` (resume=<hash>, undirectable=true,
  заголовок X-Xsrftoken из cookie `_xsrf`) — не проверен.

## Состояние кода (работает)
- `app/config.py` — настройки из env
- `app/mail.py` — IMAP: ждёт письмо от hh.ru после момента запроса, достаёт 4–6-значный код
- `app/hh.py` — Playwright persistent context (`/data/profile`), is_logged_in, login,
  raise_resumes, капча → CaptchaError (не решаем, останавливаемся), отладка в `/data/debug`
- `app/schedule.py` — разбор «можно … в HH:MM»; next_run = ближайшее разрешённое время +
  джиттер 5–180 мин (окно 4–7 ч); если уже можно, но не вышло — не позже 15 мин;
  не чаще 10 мин
- `app/main.py` — команды `run | once | check | resume`; лимит входов в день; стоп-флаг
  `/data/STOPPED` после капчи или 3 ошибок входа; Telegram-команды `/next`, `/resume`,
  `/help` (текст `TG_HELP`); `data/state.json`: logins, last_run, last_clicked,
  available_at, last_raised
- `app/notify.py` — Notifier (sendMessage) + CommandListener (long polling getUpdates
  в фоновом потоке, только из чата TG_CHAT_ID, старые команды при старте пропускаются)
- `tests/test_logic.py` — 16 тестов, проходят
- Docker: `python:3.12-slim-bookworm` + `playwright install --with-deps --only-shell chromium`
  (только headless; HEADLESS=0 — только из venv). compose: `./data:/data`, `env_file: .env`,
  `restart: unless-stopped`, без `ports`, своя bridge-сеть, `mem_limit: 700m`, `shm_size: 256m`.
- Документация: `README.md`, `docs/gmail-setup.md`, `docs/telegram-setup.md`.

## Переменные окружения (.env)
```
HH_LOGIN_EMAIL=<email, см. CLAUDE.local.md>
HH_RESUME_IDS=<hash резюме 1>,<hash резюме 2>
IMAP_PASSWORD=<пароль приложения Google, 16 символов без пробелов и кавычек>
TG_BOT_TOKEN=<токен бота>
TG_CHAT_ID=<chat_id пользователя>
TZ=Asia/Novosibirsk
DATA_DIR=/data
```
Необязательные (по умолчанию): IMAP_HOST=imap.gmail.com, IMAP_PORT=993,
IMAP_USER=HH_LOGIN_EMAIL, IMAP_FOLDER=INBOX, MAIL_SENDER_FILTER=hh.ru,
CODE_WAIT_SECONDS=180, MIN_INTERVAL_MINUTES=240, JITTER_MIN_MINUTES=5,
JITTER_MAX_MINUTES=180, MAX_LOGINS_PER_DAY=3, HEADLESS=1.

## Что осталось
1. Пользователь копирует `.env` на сервер → остановить локальный сервис, запустить на
   сервере (`check`, `up -d`), проверить логи и `/next`.
2. `gh auth login` пользователем → `git push origin master` (локально коммиты впереди).
   Решить с пользователем приватность репозитория.
3. Пользователю отозвать доступы старого hh-agent: Gmail OAuth
   (myaccount.google.com/permissions) и устройство ChatGPT/Codex.
4. Идеи (не согласованы): уведомлять, если поднять было можно, но не вышло; подробности
   в сообщении «Поднято резюме»; регистрация команд через `setMyCommands`.
