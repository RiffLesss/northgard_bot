# Northgard Discord Bot

Discord-бот для Northgard:

- `/shuffle_teams` генерирует расписание раундов и команд;
- `/start_draft_2v2` запускает ban-pick 2v2;
- `/stop_draft_2v2` останавливает текущий драфт;
- `/restart_draft_2v2` перезапускает драфт текущей игры;
- `/add_admin` добавляет администратора бота.

## Структура

```text
northgard_bot/
  __main__.py      # запуск через python -m northgard_bot
  bot.py           # Discord commands/events
  config.py        # переменные окружения
  draft.py         # ban-pick логика и Discord UI
  schedule.py      # генерация расписания
tests/             # тесты чистой логики
data/              # runtime-состояние, не коммитится
```

Старые файлы `discord_bot.py`, `draft_northgard_script.py` и `random_teams_northgard_script.py` оставлены как совместимые обертки.

## Локальный запуск

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Создайте `.env` на основе `.env.example` или задайте переменные в PowerShell:

```powershell
$env:DISCORD_BOT_TOKEN="your_token"
$env:DISCORD_GUILD_ID="123456789012345678"
$env:ALLOWED_CHANNEL_ID="1481764374569025671"
python -m northgard_bot
```

`DISCORD_BOT_TOKEN` обязателен. Остальные переменные опциональны:

- `DISCORD_GUILD_ID` ускоряет синхронизацию slash-команд на одном сервере;
- `ALLOWED_CHANNEL_ID` ограничивает `/shuffle_teams` одним каналом;
- `BOT_ADMINS_FILE` задает путь к JSON-файлу админов, по умолчанию `data/bot_admins.json`.

## Docker

```powershell
docker compose up -d --build
```

Compose монтирует volume `bot_data` в `/app/data`, поэтому список админов сохраняется между перезапусками контейнера.

## Проверки

```powershell
python -m py_compile discord_bot.py draft_northgard_script.py random_teams_northgard_script.py northgard_bot\*.py
python -m unittest discover -s tests
```

## Database migrations

PostgreSQL schema is managed by Alembic.

```powershell
alembic upgrade head
```

With Docker:

```powershell
docker compose run --rm bot alembic upgrade head
```
