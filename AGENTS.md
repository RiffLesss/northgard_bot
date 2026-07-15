# Project Guide

This file is a quick navigation index for Codex and other coding agents working on this project.
When adding or changing project documentation, update this index in the same change.

## Documentation

- [3v3 Modes](docs/team3_usage.md) - how the 3v3 panel, casual queue, ranked queue, ready checks, draft flow, result confirmation, disputes, and cleanup work.

## Project Entry Points

- `bot/main.py` - Discord bot startup and cog loading.
- `bot/cogs/` - Discord slash commands and button interactions.
- `bot/services/` - business logic.
- `bot/repositories/` - database access.
- `bot/models/` - SQLAlchemy models.
- `bot/database/` - database engine/session setup.
- `alembic/versions/` - database migrations.

## Maintenance Notes

- Keep user-facing mode instructions in `docs/`.
- Keep this file as an index only; detailed feature documentation belongs in separate markdown files under `docs/`.
- After database model changes, add an Alembic migration and document any required deployment command if needed.
