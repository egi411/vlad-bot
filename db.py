import os
import logging
from datetime import date

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.SimpleConnectionPool | None = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 2,
    project TEXT NOT NULL DEFAULT 'Клиенты',
    status TEXT NOT NULL DEFAULT 'active',
    sender TEXT NOT NULL DEFAULT 'Влад',
    due TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_COMMENTS_SQL = """
CREATE TABLE IF NOT EXISTS task_comments (
    id SERIAL PRIMARY KEY,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        database_url = os.environ["DATABASE_URL"]
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, database_url)
        logger.info("psycopg2 connection pool created")
    return _pool


def _conn():
    return _get_pool().getconn()


def _put(conn):
    _get_pool().putconn(conn)


def init_db():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(CREATE_COMMENTS_SQL)
            cur.execute(CREATE_SETTINGS_SQL)
        conn.commit()
        logger.info("DB initialised — tasks + comments tables ready")
    except Exception as e:
        logger.error(f"init_db error: {e}")
        conn.rollback()
        raise
    finally:
        _put(conn)


def create_task(content: str, priority: int, project: str, sender: str, due: str | None = None) -> dict:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tasks (content, priority, project, sender, due)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, content, priority, project, status, sender, due, created_at
                """,
                (content, priority, project, sender, due),
            )
            row = dict(cur.fetchone())
        conn.commit()
        logger.info(f"create_task id={row['id']} content={content[:40]!r}")
        return row
    except Exception as e:
        logger.error(f"create_task error: {e}")
        conn.rollback()
        raise
    finally:
        _put(conn)


def get_task(task_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, content, priority, project, status, sender, due, created_at FROM tasks WHERE id=%s",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_task error: {e}")
        return None
    finally:
        _put(conn)


def get_active_tasks() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, content, priority, project, status, sender, due, created_at
                FROM tasks
                WHERE status IN ('active', 'waiting')
                ORDER BY priority DESC, created_at ASC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_active_tasks error: {e}")
        return []
    finally:
        _put(conn)


def get_waiting_tasks() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, content, priority, project, status, sender, due, created_at
                FROM tasks
                WHERE status = 'waiting'
                ORDER BY priority DESC, created_at ASC
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_waiting_tasks error: {e}")
        return []
    finally:
        _put(conn)


def mark_waiting(task_id: int):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tasks SET status='waiting' WHERE id=%s", (task_id,))
        conn.commit()
        logger.info(f"mark_waiting id={task_id}")
    except Exception as e:
        logger.error(f"mark_waiting error: {e}")
        conn.rollback()
        raise
    finally:
        _put(conn)


def mark_done(task_id: int):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tasks SET status='done' WHERE id=%s", (task_id,))
        conn.commit()
        logger.info(f"mark_done id={task_id}")
    except Exception as e:
        logger.error(f"mark_done error: {e}")
        conn.rollback()
        raise
    finally:
        _put(conn)


def add_comment(task_id: int, author: str, text: str) -> dict:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO task_comments (task_id, author, text) VALUES (%s, %s, %s) RETURNING *",
                (task_id, author, text),
            )
            row = dict(cur.fetchone())
        conn.commit()
        logger.info(f"add_comment task_id={task_id} author={author}")
        return row
    except Exception as e:
        logger.error(f"add_comment error: {e}")
        conn.rollback()
        raise
    finally:
        _put(conn)


def get_setting(key: str) -> str | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"get_setting error: {e}")
        return None
    finally:
        _put(conn)


def set_setting(key: str, value: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (key, value),
            )
        conn.commit()
    except Exception as e:
        logger.error(f"set_setting error: {e}")
        conn.rollback()
    finally:
        _put(conn)


def get_comments(task_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM task_comments WHERE task_id=%s ORDER BY created_at ASC",
                (task_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_comments error: {e}")
        return []
    finally:
        _put(conn)


def get_completed_today() -> list[dict]:
    conn = _conn()
    try:
        today = date.today().isoformat()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, content, priority, project, status, sender, due, created_at
                FROM tasks
                WHERE status = 'done'
                  AND created_at >= %s::date
                ORDER BY created_at DESC
                """,
                (today,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_completed_today error: {e}")
        return []
    finally:
        _put(conn)
