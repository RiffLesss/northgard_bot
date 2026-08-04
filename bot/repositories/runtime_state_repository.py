from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from bot.models.runtime_state import RuntimeState


class RuntimeStateRepository:
    def __init__(self, session):
        self.session = session

    async def get(self, key: str) -> dict | None:
        state = await self.session.scalar(select(RuntimeState).where(RuntimeState.key == key))
        return state.value if state else None

    async def list_prefix(self, prefix: str) -> list[tuple[str, dict]]:
        rows = await self.session.scalars(select(RuntimeState).where(RuntimeState.key.startswith(prefix)))
        return [(state.key, state.value) for state in rows]

    async def put(self, key: str, value: dict) -> None:
        statement = insert(RuntimeState).values(key=key, value=value)
        statement = statement.on_conflict_do_update(
            index_elements=[RuntimeState.key],
            set_={"value": value},
        )
        await self.session.execute(statement)

    async def delete(self, key: str) -> None:
        await self.session.execute(delete(RuntimeState).where(RuntimeState.key == key))
