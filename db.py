import aiosqlite

from config import DB_PATH


class Database:
    def __init__(self, path=DB_PATH):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created TEXT,
                total INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self._conn.commit()
        try:
            await self._conn.execute("ALTER TABLE users ADD COLUMN created TEXT")
            await self._conn.commit()
        except aiosqlite.Error:
            pass

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def ensure_user(self, user_id: int, username: str | None) -> None:
        assert self._conn
        from datetime import date

        await self._conn.execute(
            """
            INSERT INTO users (user_id, username, created) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
            """,
            (user_id, username, date.today().isoformat()),
        )
        await self._conn.commit()

    async def consume(self, user_id: int) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE users SET total = total + 1 WHERE user_id = ?", (user_id,)
        )
        await self._conn.commit()

    async def stats(self, user_id: int) -> dict:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT total, created FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return {"total": row[0] if row else 0, "created": row[1] if row else None}

    async def global_total(self) -> int:
        assert self._conn
        cur = await self._conn.execute("SELECT COALESCE(SUM(total), 0) FROM users")
        row = await cur.fetchone()
        return int(row[0])


db = Database()
