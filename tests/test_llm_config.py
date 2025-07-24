import os
import sqlite3
import sys
import types
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import llm_config_daemon
import llm_processor


def setup_db(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
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
            payload TEXT,
            processed INTEGER DEFAULT 0,
            created_timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()
    return db


def test_llm_config_daemon_updates_notification(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor', 'input', 'results', 1)"
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
    cur.execute("SELECT needs_reload FROM llm_io_config WHERE llm_id='main_llm_processor'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM llm_notifications")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_llm_processor_reads_config_and_runs(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE input (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)")
    conn.execute("INSERT INTO input (data) VALUES ('x')")
    conn.execute("CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, llm_id TEXT, content TEXT)")
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor', 'input', 'results', 0)"
    )
    conn.execute(
        "INSERT INTO llm_notifications (llm_id, notification_type, payload) VALUES ('main_llm_processor', 'RUN', NULL)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_processor, "DB_FULL_PATH", str(db))
    monkeypatch.setattr(llm_processor, "POLL_INTERVAL", 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_processor.time, "sleep", fake_sleep)
    monkeypatch.setattr(sys, "argv", ["llm_processor.py"])  # avoid argparse parsing pytest args

    with pytest.raises(StopIteration):
        llm_processor.main()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM results")
    assert cur.fetchone()[0] >= 1
    conn.close()


def test_llm_processor_push_payload_tables(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    conn.execute("INSERT INTO a (val) VALUES ('x')")
    conn.execute("CREATE TABLE b (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    conn.execute("INSERT INTO b (val) VALUES ('y'), ('z')")
    conn.execute("CREATE TABLE out (id INTEGER PRIMARY KEY AUTOINCREMENT, llm_id TEXT, content TEXT)")
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor', 'a', 'out', 0)"
    )
    conn.execute(
        "INSERT INTO llm_notifications (llm_id, notification_type, payload) VALUES ('main_llm_processor', 'PUSH', 'b')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_processor, 'DB_FULL_PATH', str(db))
    monkeypatch.setattr(llm_processor, 'POLL_INTERVAL', 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_processor.time, 'sleep', fake_sleep)
    monkeypatch.setattr(sys, 'argv', ['llm_processor.py'])

    with pytest.raises(StopIteration):
        llm_processor.main()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute('SELECT content FROM out')
    rows = [r[0] for r in cur.fetchall()]
    conn.close()

    assert any('b rows=' in r for r in rows)


def test_llm_processor_handles_pull_request(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE src (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    conn.execute("CREATE TABLE out (id INTEGER PRIMARY KEY AUTOINCREMENT, llm_id TEXT, content TEXT)")
    conn.execute(
        "INSERT INTO llm_io_config (llm_id, read_tables, output_table, needs_reload) VALUES ('main_llm_processor', 'src', 'out', 0)"
    )
    conn.execute(
        "INSERT INTO llm_notifications (llm_id, notification_type, payload) VALUES ('main_llm_processor', 'PULL_REQUEST', 'metrics')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_processor, 'DB_FULL_PATH', str(db))
    monkeypatch.setattr(llm_processor, 'POLL_INTERVAL', 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_processor.time, 'sleep', fake_sleep)
    monkeypatch.setattr(sys, 'argv', ['llm_processor.py'])

    with pytest.raises(StopIteration):
        llm_processor.main()

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute('SELECT content FROM out')
    rows = [r[0] for r in cur.fetchall()]
    conn.close()

    assert any(r == 'REQUEST:metrics' for r in rows)
