import os
import sqlite3
import sys
import pytest

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import llm_processor
import llm_config_daemon
import nano_instance


def setup_db(tmp_path):
    db_path = tmp_path / "access.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE llm_io_config (
            llm_id TEXT PRIMARY KEY,
            read_tables TEXT,
            output_table TEXT,
            needs_reload INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE llm_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            llm_id TEXT,
            notification_type TEXT,
            processed INTEGER DEFAULT 0,
            created_timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE system_metrics_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            cpu_temp REAL,
            cpu_usage REAL,
            mem_usage REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE nano_outputs (
            id INTEGER PRIMARY KEY,
            nano_id TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            content TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE db_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            component_id TEXT,
            table_name TEXT,
            access_type TEXT
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


def test_load_config_logs_access(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload)"
        " VALUES ('main_llm_processor', 't', 'o', 0)"
    )
    conn.commit()

    monkeypatch.setattr(llm_processor, "DB_FULL_PATH", str(db))

    llm_processor.load_config(conn)

    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_io_config' AND access_type='READ' AND component_id=?",
        (llm_processor.COMPONENT_ID,)
    )
    assert cur.fetchone()[0] == 1
    conn.close()


def test_llm_config_daemon_logs_access(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload)"
        " VALUES ('main_llm_processor', 'x', 'y', 1)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_config_daemon, "DB_FULL_PATH", str(db))
    monkeypatch.setattr(llm_config_daemon, "POLL_INTERVAL", 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_config_daemon.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        llm_config_daemon.main_loop("TEST")

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_io_config' AND access_type='READ' AND component_id=?",
        (llm_config_daemon.COMPONENT_ID,)
    )
    assert cur.fetchone()[0] >= 1
    cur.execute(
        "SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_notifications' AND access_type='WRITE' AND component_id=?",
        (llm_config_daemon.COMPONENT_ID,)
    )
    assert cur.fetchone()[0] >= 1
    conn.close()


def test_nano_instance_logs_metrics_access(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO system_metrics_log (cpu_temp, cpu_usage, mem_usage) VALUES (1,1,1)")
    conn.commit()

    monkeypatch.setattr(nano_instance, "DB_FULL_PATH", str(db))

    rows = nano_instance.fetch_recent_metrics(conn, "system_metrics_log", "nano_test")

    assert rows
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM db_access_log WHERE table_name='system_metrics_log' AND access_type='READ' AND component_id=?",
        ("nano_test",),
    )
    assert cur.fetchone()[0] == 1
    conn.close()

