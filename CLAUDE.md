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
Личное (email, hash резюме, сервер, бот, git-автор) — в `CLAUDE.local.md` (не в git).
Репозиторий публичный: личные данные, IP и секреты в отслеживаемые файлы не писать.
- Аккаунт hh: соискатель, вход по email с кодом; два резюме.
- Часовой пояс: Asia/Novosibirsk. Лимит hh: ручное поднятие не чаще раза в 4 часа.
- GitHub: `https://github.com/Teyrro/resume_up_hh_ru` (публичный, remote `origin`, ветка `master`).
  Пуш из WSL пока не работает: `gh` установлен в `~/.local/bin`, но пользователь не вошёл
  (`gh auth login`). История переписана без личных данных — нужен `git push --force`;
  на сервер коммиты доставляются через `git bundle`.

## Сервер
- `ssh vpn` (адрес — в `CLAUDE.local.md`), 2 ГБ RAM, 30 ГБ диск.
- 3x-ui работает НЕ в Docker, а как systemd: `x-ui.service`, `xray.service`
  (порты 443, 2053, 2096). ufw активен (deny incoming), fail2ban на SSH.
  **Не трогать x-ui/xray, ufw/iptables/nftables, сеть хоста.**
- Docker-том `marzban_postgres_data` — чужие данные (Marzban). Docker считает его
  неиспользуемым — **не удалять, не делать `docker system prune --volumes`**.
- Старая попытка `/opt/hh-agent` (OpenClaw + ChatGPT, сервис `hh-browser`, пользователь
  `hhagent`, npm `openclaw`) удалена 16.10.2026 по просьбе пользователя.
- Проект: `/opt/hh-autoraise` (git clone + bundle). **Сервис работает на сервере** с 16.10.2026
  (`docker compose up -d`); локальный контейнер в WSL остановлен — два экземпляра =
  двойное поднятие и Telegram 409.
- Хук `protect-secrets` блокирует любые команды, где упоминается файл переменных
  (и локально, и через ssh) — его не читать, просить пользователя.

## Что известно о вёрстке hh (проверено вживую)
- Страница `https://hh.ru/applicant/my_resumes`.
- Текст до разрешения: «Поднять вручную можно 16 октября 2026 в 00:18» /
  «… сегодня в 04:24» (`data-qa="title-description"`).
- Кнопка `data-qa="resume-update-button_actions"` с текстом «Поднять автоматически» — это
  ПЛАТНОЕ автоподнятие, её не нажимать.
- Вход по почте с кодом (селекторы в `login()`) — работает, проверено 15.10.2026.
- **Кнопка ручного поднятия НЕ находится** регуляркой `_RAISE_BUTTON`: 16.10.2026 05:39
  оба резюме подняты запасным путём. Точный текст кнопки неизвестен — при следующем
  поднятии страница сохранится в `data/debug/*-raise-no-button.{png,html}` на сервере;
  по ней поправить `_RAISE_BUTTON`.
- Запасной путь `POST /applicant/resumes/touch` (resume=<hash>, undirectable=true,
  заголовок X-Xsrftoken из cookie `_xsrf`) — **работает** (16.10.2026).

## Состояние кода (работает)
- `app/config.py` — настройки из env; REQUIRED_VARS/OPTIONAL_VARS, missing_vars()
- `app/mail.py` — IMAP: ждёт письмо от hh.ru после момента запроса, достаёт 4–6-значный код
- `app/hh.py` — Playwright persistent context (`/data/profile`), полный Chromium
  (`channel="chromium"`, новый headless); UA собирается из `chrome --version`
  («Chrome/<major>.0.0.0», Linux, без HeadlessChrome) — совпадает с userAgentData и Sec-CH-UA;
  экран 1536x864, окно 1536x730. «Человеческая» механика: мышь по кривой Безье
  (`_human_click` с проверкой elementFromPoint, иначе обычный click), прокрутка колесом,
  неровный набор (`_human_type`), `_look_around`, в 40% проходов вход через главную.
  is_logged_in, login, raise_resumes (без лишней перезагрузки «Моих резюме»),
  капча → CaptchaError (не решаем, останавливаемся), отладка в `/data/debug`
- `app/schedule.py` — разбор «можно … в HH:MM»; next_run = ближайшее разрешённое время +
  джиттер 5–180 мин (окно 4–7 ч); если уже можно, но не вышло — не позже 15 мин;
  не чаще 10 мин
- `app/settings.py` — тихие часы: parse/format/in_quiet/apply_quiet (конец окна + 0–45 мин),
  хранение в `data/settings.json`; по умолчанию выключены
- `app/main.py` — команды `run | once | check | resume`; лимит входов в день; стоп-флаг
  `/data/STOPPED` после капчи или 3 ошибок входа; Telegram-команды `/menu` (inline-кнопки:
  пресеты 23–08/22–09/00–08, «Своё время», «Выключить», «Обновить»), `/next`, `/resume`,
  `/help` (`TG_HELP`), setMyCommands при старте; смена тихих часов пересчитывает план
  (Event `replan`) без лишнего прохода; `data/state.json`: logins, last_run, last_clicked,
  available_at, last_raised
- `app/notify.py` — Notifier (send/edit/answer_callback, Reply с markup) + CommandListener
  (long polling getUpdates: message + callback_query, только из чата TG_CHAT_ID,
  `awaiting` — ввод своего значения следующим сообщением; старые команды пропускаются)
- `tests/` — 26 тестов, проходят (test_logic.py, test_env.py)
- Docker: `python:3.12-slim-bookworm` + `playwright install --with-deps --no-shell chromium`
  (HEADLESS=0 — только из venv). compose: `./data:/data`, `env_file`, `restart: unless-stopped`,
  без `ports`, своя bridge-сеть, `mem_limit: 700m`, `shm_size: 256m`.
- Документация: `README.md`, `docs/gmail-setup.md`, `docs/telegram-setup.md`.

## Переменные окружения (.env)
```
HH_LOGIN_EMAIL=<email аккаунта hh, см. CLAUDE.local.md>
HH_RESUME_IDS=<hash резюме через запятую, см. CLAUDE.local.md>
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
1. Поправить `_RAISE_BUTTON` по `data/debug/*-raise-no-button.html` с сервера.
2. Пуш на GitHub: обычный push разрешён, force — нет. Пользователь пересоздаёт
   репозиторий (или сам делает `git push --force-with-lease`), затем `git push origin master`.
3. Пользователю отозвать доступы старого hh-agent: Gmail OAuth
   (myaccount.google.com/permissions) и устройство ChatGPT/Codex.
4. Идеи (не согласованы): уведомлять, если поднять было можно, но не вышло; подробности
   в сообщении «Поднято резюме».
