# hh-autoraise

Сервис, который автоматически поднимает резюме на hh.ru в поиске — как кнопка
«Поднять» в личном кабинете соискателя, по мере того как hh.ru это разрешает
(hh.ru разрешает не чаще раза в 4 часа; сервис поднимает в случайный момент
через 4–7 часов, чтобы не выглядеть роботом).

Если сессия hh.ru слетела, сервис сам входит по почте: запрашивает код, читает письмо
от hh.ru по IMAP и вводит код. О событиях пишет в Telegram.

> API hh.ru для соискателей закрыт с 15.12.2025, поэтому используется браузерная
> автоматизация (Playwright + Chromium). Платное «Поднять автоматически» не нажимается.

## Как это работает

1. Открывает `https://hh.ru/applicant/my_resumes` в сохранённом профиле браузера.
2. Если не авторизован — вход по почте с кодом из письма (не больше `MAX_LOGINS_PER_DAY` в сутки).
3. Нажимает «Поднять» у резюме из `HH_RESUME_IDS`, у которых поднятие уже доступно.
4. По тексту «Поднять вручную можно … в HH:MM» планирует следующий запуск (+ случайная
   задержка `JITTER_*`, но не раньше `MIN_INTERVAL_MINUTES`).
5. При капче или 3 ошибках входа подряд останавливается и ждёт `/resume` в Telegram
   или ручного `resume`.

## Требования

| Что | Зачем |
|---|---|
| Docker + Docker Compose v2 | запуск сервиса (рекомендуется) |
| Аккаунт соискателя на hh.ru со входом по email | поднимаемые резюме |
| Почта с доступом по IMAP (Gmail) | получение кода входа |
| Telegram-бот (необязательно) | уведомления |
| Python 3.12 (только для локального запуска без Docker) | разработка/отладка |

Сервер: любой Linux с Docker, ~1.5 ГБ на диске (образ ~1.3 ГБ), до 700 МБ RAM во время
прохода (лимит `mem_limit` в `docker-compose.yml`; в простое ~110 МБ), исходящий доступ к
`hh.ru`, `imap.gmail.com:993`, `api.telegram.org`. Входящие порты не нужны.

## Предварительная настройка внешних сервисов

### 1. hh.ru

1. Убедиться, что вход в аккаунт соискателя возможен **по email с кодом**
   (на странице https://hh.ru/account/login вкладка «Почта»).
2. Узнать hash резюме: открыть резюме, адрес вида `https://hh.ru/resume/<hash>` —
   скопировать `<hash>`. Несколько резюме — через запятую в `HH_RESUME_IDS`.

### 2. Gmail (IMAP) — обязательно

Нужен **пароль приложения** Google, обычный пароль не подойдёт.
Пошагово: **[docs/gmail-setup.md](docs/gmail-setup.md)**.

Кратко:
1. Включить двухэтапную аутентификацию — https://myaccount.google.com/security
   ([справка Google](https://support.google.com/accounts/answer/185839)).
2. Создать пароль приложения — https://myaccount.google.com/apppasswords
   ([справка Google](https://support.google.com/accounts/answer/185833)).
3. Записать его в `IMAP_PASSWORD` одной строкой, без пробелов и кавычек.
4. IMAP в настройках Gmail включать не нужно — он включён всегда
   ([справка Google по IMAP](https://support.google.com/mail/answer/7126229)).

Другая почта: задать `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD` своего провайдера.

### 3. Telegram — необязательно

Пошагово: **[docs/telegram-setup.md](docs/telegram-setup.md)**.
Кратко: бот через [@BotFather](https://t.me/BotFather) → `TG_BOT_TOKEN`;
написать боту и взять chat_id из `getUpdates` → `TG_CHAT_ID`.
Бот также принимает команды `/next` (когда следующее поднятие), `/resume` (снять стоп)
и `/help` — только из вашего чата.

## Конфигурация (`.env`)

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `HH_LOGIN_EMAIL` | да | — | email для входа на hh.ru |
| `HH_RESUME_IDS` | да | — | hash резюме через запятую |
| `IMAP_PASSWORD` | да | — | пароль приложения Gmail (16 символов без пробелов) |
| `IMAP_HOST` | | `imap.gmail.com` | IMAP-сервер |
| `IMAP_PORT` | | `993` | порт (SSL) |
| `IMAP_USER` | | `HH_LOGIN_EMAIL` | логин почты |
| `IMAP_FOLDER` | | `INBOX` | папка с письмами hh.ru |
| `MAIL_SENDER_FILTER` | | `hh.ru` | фильтр отправителя письма с кодом |
| `CODE_WAIT_SECONDS` | | `180` | сколько ждать письмо с кодом |
| `TG_BOT_TOKEN` | | пусто | токен бота; пусто — без уведомлений |
| `TG_CHAT_ID` | | пусто | chat_id получателя |
| `TZ` | | `Asia/Novosibirsk` | часовой пояс (время из текстов hh.ru) |
| `MIN_INTERVAL_MINUTES` | | `240` | минимальный интервал между запусками |
| `JITTER_MIN_MINUTES` / `JITTER_MAX_MINUTES` | | `5` / `180` | случайная задержка после разрешённого времени: поднятие через 4–7 ч вместо ровно 4 ч |
| `MAX_LOGINS_PER_DAY` | | `3` | лимит входов по коду в сутки |
| `HEADLESS` | | `1` | `0` — показать окно браузера (только локальный запуск из venv; в Docker-образе есть только headless Chromium) |
| `DATA_DIR` | | `/data` | профиль браузера, состояние, отладка |

`.env` содержит секреты и в git не попадает (`.gitignore`).

## Развёртывание на сервере (Docker)

```bash
git clone git@github.com:Teyrro/hh-autoraise.git
cd hh-autoraise
cp .env.example .env && nano .env && chmod 600 .env
docker compose build
```

Проверка настроек (IMAP, Telegram, наличие сессии hh.ru; код входа **не** запрашивает):

```bash
docker compose run --rm hh-autoraise check
```

Ожидается `IMAP: OK`, `Telegram: отправлено тестовое сообщение`.

Первый проход вручную (вход по коду + поднятие):

```bash
docker compose run --rm hh-autoraise once
```

Запуск сервиса в фоне (перезапускается сам, `restart: unless-stopped`):

```bash
docker compose up -d
docker compose logs -f
```

Контейнер работает в собственной bridge-сети `hh-autoraise`, портов не открывает и не
затрагивает другие контейнеры на сервере. Перед запуском на общем сервере проверьте, что
имена `hh-autoraise` (контейнер, сеть) не заняты: `docker ps -a`, `docker network ls`.

Данные хранятся в `./data` на хосте:

| Путь | Что |
|---|---|
| `data/profile/` | профиль Chromium с сессией hh.ru |
| `data/state.json` | время последних входов и запусков |
| `data/debug/` | скриншоты и HTML при ошибках |
| `data/STOPPED` | флаг остановки после капчи/ошибок входа |

Перенос сессии с другой машины: скопировать `data/profile/` (при остановленном
контейнере). Без этого сервис просто выполнит вход по коду при первом запуске.

## Эксплуатация

| Действие | Команда |
|---|---|
| Логи | `docker compose logs --tail 50` |
| Остановить | `docker compose down` |
| Перезапустить | `docker compose restart` |
| Один проход вручную | `docker compose run --rm hh-autoraise once` |
| Снять стоп после капчи | команда `/resume` боту в Telegram, или `docker compose run --rm hh-autoraise resume && docker compose restart` |

Обновление:

```bash
git pull
docker compose build
docker compose up -d
```

### Капча и остановка

Если hh.ru показал капчу или вход не удался 3 раза подряд, сервис создаёт `data/STOPPED`,
шлёт уведомление и перестаёт что-либо делать (чтобы не получить блокировку).
Что делать:

1. Посмотреть `data/debug/` (скриншот страницы) и логи.
2. При необходимости войти на hh.ru вручную в обычном браузере, подождать несколько часов.
3. Снять стоп: отправить боту `/resume` (сервис сразу сделает проход) или
   `docker compose run --rm hh-autoraise resume && docker compose restart`.

## Частые проблемы

| Симптом | Решение |
|---|---|
| `IMAP: ОШИБКА — ... AUTHENTICATIONFAILED` | нужен пароль приложения, см. [docs/gmail-setup.md](docs/gmail-setup.md) |
| `Не задана переменная окружения ...` | не заполнен `.env` |
| Код не находится в почте | письмо в «Спаме»/другой папке — `IMAP_FOLDER`, `MAIL_SENDER_FILTER` |
| Telegram молчит | не нажат Start у бота или неверный `TG_CHAT_ID`, см. [docs/telegram-setup.md](docs/telegram-setup.md) |
| `поднято=0`, «Поднять вручную можно …» | норма: hh.ru ещё не разрешает, следующий запуск запланирован |
| Ошибка входа / не найдено поле | hh.ru поменял вёрстку — смотреть `data/debug/`, править селекторы в `app/hh.py` |

## Локальный запуск без Docker (разработка)

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt pytest
venv/bin/playwright install --with-deps chromium
set -a; . ./.env; set +a
DATA_DIR=./data HEADLESS=0 venv/bin/python -m app.main once
```

Команды: `run` (цикл), `once`, `check`, `resume`.

Тесты:

```bash
venv/bin/python -m pytest -q tests
```

## Структура

```
app/
  config.py    настройки из переменных окружения
  hh.py        Playwright: проверка сессии, вход по коду, поднятие резюме
  mail.py      IMAP: ожидание письма hh.ru и извлечение кода
  schedule.py  разбор «можно … в HH:MM», расчёт следующего запуска
  notify.py    Telegram: уведомления и команды (/next, /resume, /help)
  main.py      точка входа: run | once | check | resume
docs/
  gmail-setup.md     настройка Gmail (пароль приложения, IMAP)
  telegram-setup.md  настройка Telegram-бота
tests/               тесты логики (pytest)
```
