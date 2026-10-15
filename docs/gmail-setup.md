# Настройка Gmail (IMAP) для кодов входа hh.ru

Если сессия hh слетела, бот запрашивает код входа на почту и читает письмо от hh.ru
по IMAP (`app/mail.py`). Для Gmail обычный пароль аккаунта по IMAP не работает —
нужен **пароль приложения** (App Password).

## 1. Включить двухэтапную аутентификацию

Без неё пароль приложения создать нельзя.

1. Открыть https://myaccount.google.com/security
   ([справка Google](https://support.google.com/accounts/answer/185839))
2. «Двухэтапная аутентификация» → «Включить», пройти шаги.

## 2. Создать пароль приложения

1. Открыть https://myaccount.google.com/apppasswords
   ([справка Google](https://support.google.com/accounts/answer/185833))
2. Ввести название, например `hh-autoraise`, → «Создать».
3. Скопировать 16-символьный пароль (`abcd efgh ijkl mnop`) и убрать пробелы.
   Google показывает его один раз — если потерян, удалить и создать новый.

Если страницы нет:
- не включена двухэтапная аутентификация;
- аккаунт Google Workspace и администратор запретил пароли приложений;
- включена «Расширенная защита» (Advanced Protection).

## 3. IMAP в настройках Gmail

Ничего делать не нужно: с конца 2024 года IMAP в Gmail включён всегда, переключателя
«Включить IMAP» больше нет. В «Настройки → Пересылка и POP/IMAP» остались только
параметры удаления и ограничений папок — их не трогать.

## 4. Заполнить `.env`

```bash
cp .env.example .env
```

```
HH_LOGIN_EMAIL=you@gmail.com
IMAP_PASSWORD=abcdefghijklmnop
```

Остальные IMAP-переменные необязательны (значения по умолчанию из `app/config.py`):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `IMAP_HOST` | `imap.gmail.com` | IMAP-сервер |
| `IMAP_PORT` | `993` | порт, SSL/TLS |
| `IMAP_USER` | `HH_LOGIN_EMAIL` | логин — полный адрес почты |
| `IMAP_PASSWORD` | — (обязательно) | пароль приложения из шага 2 |
| `IMAP_FOLDER` | `INBOX` | папка, где искать письмо |
| `MAIL_SENDER_FILTER` | `hh.ru` | фильтр отправителя (`FROM`) |
| `CODE_WAIT_SECONDS` | `180` | сколько ждать письмо с кодом |

Если письма hh.ru фильтром Gmail уходят в другую папку/ярлык — указать её в
`IMAP_FOLDER` (например `[Gmail]/All Mail` для «Вся почта»).

## 5. Проверка

В Docker:

```bash
docker compose run --rm hh-autoraise check
```

Локально (из venv):

```bash
python -m app.main check
```

Ожидаемый вывод: `IMAP: OK`.

## Частые ошибки

| Ошибка | Причина |
|---|---|
| `AUTHENTICATIONFAILED` / `Invalid credentials` | указан обычный пароль, пароль скопирован с ошибкой или отозван |
| `Не задана переменная окружения IMAP_PASSWORD` | `.env` не заполнен или не подхвачен (`env_file: .env` в `docker-compose.yml`) |
| `IMAP: OK`, но код не находится | письмо попало в «Спам» или другую папку — проверить `IMAP_FOLDER` и `MAIL_SENDER_FILTER` |

Пароль приложения перестаёт работать после смены пароля Google-аккаунта — тогда
создать новый и обновить `IMAP_PASSWORD`.
