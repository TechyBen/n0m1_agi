import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import manager_utils
import llm_processor
import llm_config_daemon
import nano_instance


def setup_db(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE db_access_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP, component_id TEXT, table_name TEXT, access_type TEXT)"""
    )
    conn.execute(
        """CREATE TABLE llm_io_config (llm_id TEXT PRIMARY KEY, read_tables TEXT, output_table TEXT, needs_reload INTEGER)"""
    )
    conn.execute(
        """CREATE TABLE llm_notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, llm_id TEXT, notification_type TEXT, processed INTEGER DEFAULT 0, created_timestamp TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    conn.execute(
        """CREATE TABLE system_metrics_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP, cpu_temp REAL, cpu_usage REAL, mem_usage REAL)"""
    )
    conn.execute(
        """CREATE TABLE nano_outputs (id INTEGER PRIMARY KEY AUTOINCREMENT, nano_id TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP, content TEXT)"""
    )
    conn.commit()
    conn.close()
    return db


def test_log_db_access_function(tmp_path):
    db = setup_db(tmp_path)
    assert manager_utils.log_db_access(str(db), "comp", "tbl", "READ")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT component_id, table_name, access_type FROM db_access_log")
    row = cur.fetchone()
    conn.close()
    assert row == ("comp", "tbl", "READ")


def test_llm_processor_load_config_logs(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor','a','b',0)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_processor, "DB_FULL_PATH", str(db))

    with sqlite3.connect(db) as conn:
        llm_processor.load_config(conn)

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_io_config'")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1


def test_llm_config_daemon_logs_read(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor','x','y',1)")
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
    cur.execute("SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_io_config'")
    count = cur.fetchone()[0]
    conn.close()
    assert count >= 1


def test_nano_instance_logs_read(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO system_metrics_log (cpu_temp, cpu_usage, mem_usage) VALUES (10,1,1)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(nano_instance, "DB_FULL_PATH", str(db))
    monkeypatch.setattr(nano_instance, "METRICS_TABLE", "system_metrics_log")

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(nano_instance.time, "sleep", fake_sleep)
    monkeypatch.setattr(sys, "argv", ["nano_instance.py"])

    with pytest.raises(StopIteration):
        nano_instance.main()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM db_access_log WHERE table_name='system_metrics_log'")
    count = cur.fetchone()[0]
    conn.close()
    assert count >= 1
