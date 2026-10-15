# Настройка Telegram-уведомлений (необязательно)

Сервис шлёт в Telegram: запуск, повторный вход на hh.ru, успешное поднятие резюме,
капчу и ошибки входа. Попытки, когда поднимать ещё рано, не отправляются.
Входящие сообщения бот не читает и на команды не отвечает.

Если `TG_BOT_TOKEN` или `TG_CHAT_ID` пустые — уведомления выключены, сервис работает.

## 1. Создать бота — `TG_BOT_TOKEN`

1. Открыть [@BotFather](https://t.me/BotFather), отправить `/newbot`.
2. Задать имя и username (должен заканчиваться на `bot`).
3. BotFather пришлёт токен вида `123456789:AAH...` — это `TG_BOT_TOKEN`.

Документация: https://core.telegram.org/bots/tutorial#obtain-your-bot-token

## 2. Узнать chat_id — `TG_CHAT_ID`

1. Найти своего бота в Telegram, нажать **Start** и написать ему любое сообщение.
   Без этого бот не может писать вам первым.
2. Выполнить (вместо `<TOKEN>` — токен):

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":-\?[0-9]*' | head -n 1
   ```

3. Число после `"id":` — это `TG_CHAT_ID`.

Если ответ пустой (`"result":[]`) — боту ещё не писали, отправьте сообщение и повторите.

## 3. Заполнить `.env`

```
TG_BOT_TOKEN=123456789:AAH...
TG_CHAT_ID=123456789
```

Проверка: `docker compose run --rm hh-autoraise check` — в чат придёт
«hh-autoraise: Проверка связи: Telegram работает.»
