# hh-autoraise — контекст для продолжения работы

## Цель
Автономный сервис на собственном сервере: каждые ~4 часа поднимает резюме на hh.ru.
Если сессия hh слетела — сам входит по почте: запрашивает код, читает письмо из Gmail
по IMAP, вводит код. Затем нажимает «Поднять» в обоих резюме.

## Почему не через API
dev.hh.ru: «поддержка API для соискателей прекращена 15 декабря 2025» — токен соискателя
получить нельзя, поэтому браузерная автоматизация (Playwright).

## Данные
- Аккаунт hh: соискатель, почта для входа и кодов — <email, см. CLAUDE.local.md>
- Резюме (hash):
  - Python Backend-разработчик — `<hash резюме 1>`
  - Support L2 / Application Support — `<hash резюме 2>`
- Часовой пояс: Asia/Novosibirsk. Лимит hh: ручное поднятие не чаще раза в 4 часа.
- GitHub: Teyrro, репозиторий планировался приватный `Teyrro/hh-autoraise` (ещё не создан).
- Сервер: VPN на 3x-ui в Docker. SSH-конфиг лежит в WSL на ПК пользователя.
  **Не трогать 3x-ui, его контейнеры, сеть хоста и iptables.** Сервис — отдельный
  контейнер без проброса портов.

## Что известно о вёрстке hh (проверено в браузере)
- Страница `https://hh.ru/applicant/my_resumes`.
- Текст до разрешения: «Поднять вручную можно 16 октября 2026 в 00:18» (`data-qa="title-description"`).
- Кнопка `data-qa="resume-update-button_actions"` с текстом «Поднять автоматически» — это
  ПЛАТНОЕ автоподнятие, её не нажимать. Текст кнопки, когда ручное поднятие доступно,
  не проверен (предположительно «Поднять» / «Поднять в поиске»).
- Страница входа `/account/login`: SSR отдаёт только `account-login-form`,
  `account-type-card-APPLICANT`, `account-type-card-EMPLOYER`, `submit-button`;
  остальное (вкладки Телефон/Почта, поле, «Дальше», поле кода) рендерится JS —
  селекторы НЕ проверены, подобраны эвристически в `app/hh.py`.
- Запасной путь поднятия: `POST /applicant/resumes/touch` (resume=<hash>, undirectable=true,
  заголовок X-Xsrftoken из cookie `_xsrf`) — по памяти, не проверено.

## Состояние кода
Написано, но НИ РАЗУ не запускалось:
- `app/config.py` — настройки из env (см. переменные ниже)
- `app/mail.py` — IMAP: ждёт письмо от hh.ru после момента запроса, достаёт 4–6-значный код
- `app/hh.py` — Playwright persistent context (`/data/profile`), is_logged_in, login, raise_resumes,
  капча → CaptchaError (не решаем, останавливаемся), отладка в `/data/debug` (png+html)
- `app/schedule.py` — разбор «можно … в HH:MM», next_run с джиттером, не чаще 10 мин
- `app/main.py` — команды `run | once | check | resume`; лимит входов в день; стоп-флаг
  `/data/STOPPED` после капчи или 3 ошибок входа; уведомления Telegram
- `tests/test_logic.py` — тесты разбора дат и кода (не запускались)

## Переменные окружения (.env)
```
HH_LOGIN_EMAIL=<email, см. CLAUDE.local.md>
HH_RESUME_IDS=<hash резюме 1>,<hash резюме 2>
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=<email, см. CLAUDE.local.md>
IMAP_PASSWORD=<пароль приложения Google, 16 символов>
MAIL_SENDER_FILTER=hh.ru
TG_BOT_TOKEN=
TG_CHAT_ID=
TZ=Asia/Novosibirsk
DATA_DIR=/data
```

## Что осталось
1. `pytest tests` — починить, если упадёт.
2. Локально (HEADLESS=0) пройти вход на hh, поправить селекторы в `login()` по реальной
   странице; проверить текст кнопки ручного поднятия.
3. Dockerfile на `mcr.microsoft.com/playwright/python:v1.56.0-noble` + `playwright==1.56.0`,
   docker-compose: том `./data:/data`, `env_file: .env`, `restart: unless-stopped`, без `ports`,
   свой bridge-network (не host).
4. README: пароль приложения Gmail (2FA → myaccount.google.com/apppasswords), Telegram-бот
   (@BotFather → токен; chat_id через getUpdates), установка и обновление.
5. Создать приватный репозиторий, запушить, развернуть на сервере (сначала `docker ps`,
   проверить занятые имена/порты, ничего не менять у 3x-ui).
