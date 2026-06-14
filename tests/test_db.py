"""
Tests for db.py — all psycopg2 I/O is mocked so no real DB is needed.
"""
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Stub psycopg2 before db is imported so the module-level pool creation
# doesn't blow up when there's no real psycopg2 installed.
# ---------------------------------------------------------------------------
pg2_stub = types.ModuleType("psycopg2")
pg2_stub.extras = types.ModuleType("psycopg2.extras")
pg2_stub.extras.RealDictCursor = object
pg2_stub.pool = types.ModuleType("psycopg2.pool")
pg2_stub.pool.SimpleConnectionPool = MagicMock()
sys.modules.setdefault("psycopg2", pg2_stub)
sys.modules.setdefault("psycopg2.extras", pg2_stub.extras)
sys.modules.setdefault("psycopg2.pool", pg2_stub.pool)

# Ensure vlad-bot directory is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402  (import after stub setup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cursor(fetchone_result=None, fetchall_result=None):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = fetchone_result
    cur.fetchall.return_value = fetchall_result or []
    return cur


def _make_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_insert_called_with_correct_params(self):
        row = {
            "id": 1, "content": "Test task", "priority": 3,
            "project": "Контент", "status": "active",
            "sender": "Влад", "due": None, "created_at": "2026-01-01"
        }
        cur = _make_cursor(fetchone_result=row)
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            result = db.create_task(
                content="Test task",
                priority=3,
                project="Контент",
                sender="Влад",
                due=None,
            )

        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert "INSERT INTO tasks" in sql
        assert params == ("Test task", 3, "Контент", "Влад", None)
        assert result["id"] == 1
        conn.commit.assert_called_once()

    def test_create_task_with_due(self):
        row = {
            "id": 2, "content": "Buy milk", "priority": 2,
            "project": "Личное", "status": "active",
            "sender": "Виктория", "due": "2026-06-20", "created_at": "2026-06-14"
        }
        cur = _make_cursor(fetchone_result=row)
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            result = db.create_task("Buy milk", 2, "Личное", "Виктория", due="2026-06-20")

        _, params = cur.execute.call_args[0]
        assert params[4] == "2026-06-20"
        assert result["due"] == "2026-06-20"


class TestGetActiveTasks:
    def test_returns_list_from_fetchall(self):
        rows = [
            {"id": 1, "content": "A", "priority": 4, "project": "P", "status": "active", "sender": "Влад", "due": None, "created_at": "2026-01-01"},
            {"id": 2, "content": "B", "priority": 2, "project": "P", "status": "waiting", "sender": "Влад", "due": None, "created_at": "2026-01-02"},
        ]
        cur = _make_cursor(fetchall_result=rows)
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            result = db.get_active_tasks()

        sql = cur.execute.call_args[0][0]
        assert "status IN ('active', 'waiting')" in sql
        assert len(result) == 2

    def test_returns_empty_list_on_error(self):
        conn = MagicMock()
        conn.cursor.side_effect = Exception("DB down")

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            result = db.get_active_tasks()

        assert result == []


class TestGetWaitingTasks:
    def test_where_status_waiting(self):
        rows = [
            {"id": 5, "content": "Waiting task", "priority": 3, "project": "P",
             "status": "waiting", "sender": "Влад", "due": None, "created_at": "2026-01-01"},
        ]
        cur = _make_cursor(fetchall_result=rows)
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            result = db.get_waiting_tasks()

        sql = cur.execute.call_args[0][0]
        assert "status = 'waiting'" in sql
        assert len(result) == 1
        assert result[0]["content"] == "Waiting task"


class TestMarkWaiting:
    def test_update_status_waiting(self):
        cur = _make_cursor()
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            db.mark_waiting(42)

        sql, params = cur.execute.call_args[0]
        assert "status='waiting'" in sql
        assert params == (42,)
        conn.commit.assert_called_once()


class TestMarkDone:
    def test_update_status_done(self):
        cur = _make_cursor()
        conn = _make_conn(cur)

        with patch.object(db, "_conn", return_value=conn), \
             patch.object(db, "_put"):
            db.mark_done(7)

        sql, params = cur.execute.call_args[0]
        assert "status='done'" in sql
        assert params == (7,)
        conn.commit.assert_called_once()
