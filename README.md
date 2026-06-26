# Northgard Discord Bot

Discord bot for Northgard drafts, registration, PostgreSQL-backed data, and the Bear Ladder.

## Structure

```text
bot/
  main.py              # bot setup and cog registration
  run.py               # startup entrypoint, optional test run
  cogs/                # Discord slash commands
  services/            # business logic
  repositories/        # database access
  models/              # SQLAlchemy models
  database/            # engine/session setup
alembic/               # database migrations
tests/                 # unit tests
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.example` and set at least:

```env
DISCORD_BOT_TOKEN=your_token
DISCORD_GUILD_ID=your_guild_id
DATABASE_URL=postgresql://northgard_bot:northgard_bot_pass@postgres:5432/northgard_bot
```

Local run without Docker:

```powershell
python -m bot.run
```

## Docker

Start bot and PostgreSQL:

```powershell
docker compose up -d --build
```

Apply migrations:

```powershell
docker compose run --rm --no-deps bot alembic upgrade head
```

## Tests

```powershell
python -m pytest
```

or:

```powershell
python -m unittest discover -s tests
```

To run tests automatically before bot startup, set:

```env
RUN_TESTS_ON_STARTUP=true
```
