import os
import sqlite3
import sys
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cpu_usage_daemon
import mem_usage_daemon


def setup_db(tmp_path, table_sql):
    db_path = tmp_path / "metrics.db"
    conn = sqlite3.connect(db_path)
    conn.execute(table_sql)
    conn.commit()
    conn.close()
    return str(db_path)


def test_cpu_usage_daemon_writes_rows(tmp_path, monkeypatch):
    sql = """CREATE TABLE cpu_usage_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, cpu_usage REAL)"""
    db_path = setup_db(tmp_path, sql)
    monkeypatch.setattr(cpu_usage_daemon, "DB_FULL_PATH", db_path)
    monkeypatch.setattr(cpu_usage_daemon, "POLLING_INTERVAL_SECONDS", 0)

    if cpu_usage_daemon.psutil is None:
        dummy = types.SimpleNamespace(cpu_percent=lambda interval=None: 1.0)
        monkeypatch.setattr(cpu_usage_daemon, "psutil", dummy, raising=False)
    else:
        monkeypatch.setattr(cpu_usage_daemon.psutil, "cpu_percent", lambda interval=None: 1.0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(cpu_usage_daemon.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        cpu_usage_daemon.main_loop("TEST")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM cpu_usage_log")
    count = cur.fetchone()[0]
    conn.close()

    assert count >= 1


def test_mem_usage_daemon_writes_rows(tmp_path, monkeypatch):
    sql = """CREATE TABLE memory_usage_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, mem_usage REAL)"""
    db_path = setup_db(tmp_path, sql)
    monkeypatch.setattr(mem_usage_daemon, "DB_FULL_PATH", db_path)
    monkeypatch.setattr(mem_usage_daemon, "POLLING_INTERVAL_SECONDS", 0)

    if mem_usage_daemon.psutil is None:
        dummy = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(percent=2.0))
        monkeypatch.setattr(mem_usage_daemon, "psutil", dummy, raising=False)
    else:
        monkeypatch.setattr(mem_usage_daemon.psutil, "virtual_memory", lambda: types.SimpleNamespace(percent=2.0))

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(mem_usage_daemon.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        mem_usage_daemon.main_loop("TEST")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memory_usage_log")
    count = cur.fetchone()[0]
    conn.close()

    assert count >= 1
