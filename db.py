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
        conn.commit()
        logger.info("DB initialised — tasks table ready")
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
