import os
import logging

logger = logging.getLogger(__name__)

# تلاش برای استفاده از PostgreSQL
USE_POSTGRES = False
_pool = None
_db_url = None

try:
    import asyncpg
    USE_POSTGRES = True
except ImportError:
    logger.warning("asyncpg نصب نیست، از SQLite استفاده می‌شه")
    USE_POSTGRES = False


async def init_database():
    """راه‌اندازی دیتابیس"""
    global _pool, _db_url

    if not USE_POSTGRES:
        # SQLite
        import sqlite3
        conn = sqlite3.connect("private_bot.db")
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                phone TEXT,
                api_id INTEGER,
                api_hash TEXT,
                session_string TEXT,
                account_name TEXT,
                username TEXT,
                created_date TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                title TEXT,
                text TEXT,
                created_date TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                owner_id INTEGER PRIMARY KEY,
                current_account_id INTEGER,
                current_template_id INTEGER,
                channel_id TEXT,
                channel_title TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("SQLite آماده شد")
        return

    # PostgreSQL
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)

    _pool = await asyncpg.create_pool(_db_url, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT,
                phone TEXT,
                api_id INTEGER,
                api_hash TEXT,
                session_string TEXT,
                account_name TEXT,
                username TEXT,
                created_date TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT,
                title TEXT,
                text TEXT,
                created_date TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                owner_id BIGINT PRIMARY KEY,
                current_account_id INTEGER,
                current_template_id INTEGER,
                channel_id TEXT,
                channel_title TEXT
            )
        ''')

    logger.info("PostgreSQL آماده شد")


# ==================== SQLite Fallback ====================
def _sqlite_conn():
    import sqlite3
    return sqlite3.connect("private_bot.db")


async def db_execute(query, params=None, fetch=False, fetch_one=False):
    """اجرای کوئری روی دیتابیس فعال"""
    if USE_POSTGRES and _pool:
        # تبدیل ؟ به $1، $2 برای Postgres
        if params:
            for i in range(len(params), 0, -1):
                query = query.replace('?', f'${i}', 1)

        async with _pool.acquire() as conn:
            if fetch_one:
                return await conn.fetchrow(query, *params if params else [])
            elif fetch:
                return await conn.fetch(query, *params if params else [])
            else:
                # INSERT با RETURNING
                if 'RETURNING' in query.upper():
                    return await conn.fetchval(query, *params if params else [])
                await conn.execute(query, *params if params else [])
                return None
    else:
        # SQLite
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, _sqlite_execute, query, params, fetch, fetch_one
        )


def _sqlite_execute(query, params, fetch, fetch_one):
    conn = _sqlite_conn()
    c = conn.cursor()
    try:
        if params:
            c.execute(query, params)
        else:
            c.execute(query)

        result = None
        if fetch_one:
            result = c.fetchone()
        elif fetch:
            result = c.fetchall()
        else:
            if 'RETURNING' in query.upper():
                result = c.fetchone()
                if result:
                    result = result[0]
            else:
                conn.commit()
                result = c.lastrowid
        conn.commit()
        return result
    finally:
        conn.close()
