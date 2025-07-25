import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import llm_command_daemon


def setup_db(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE llm_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            llm_id TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            content TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE autorun_components (
            component_id TEXT PRIMARY KEY,
            base_script_name TEXT,
            manager_affinity TEXT,
            desired_state TEXT
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
    return db


def test_command_starts_component(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO llm_outputs (llm_id, content) VALUES (?, ?)",
        ('main_llm_processor', 'CMD:START test_comp')
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_command_daemon, 'DB_FULL_PATH', str(db))
    monkeypatch.setattr(llm_command_daemon, 'POLL_INTERVAL', 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_command_daemon.time, 'sleep', fake_sleep)

    with pytest.raises(StopIteration):
        llm_command_daemon.main_loop('TEST')

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT desired_state FROM autorun_components WHERE component_id='test_comp'")
    row = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM db_access_log WHERE table_name='llm_outputs'")
    reads = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM db_access_log WHERE table_name='autorun_components'")
    writes = cur.fetchone()[0]
    conn.close()

    assert row is not None and row[0] == 'active'
    assert reads >= 1
    assert writes >= 1


def test_command_updates_existing_component(tmp_path, monkeypatch):
    db = setup_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO autorun_components (component_id, base_script_name, manager_affinity, desired_state) VALUES (?, ?, ?, ?)",
        ("test_comp2", "test_comp2.py", "daemon_manager", "inactive"),
    )
    conn.execute(
        "INSERT INTO llm_outputs (llm_id, content) VALUES (?, ?)",
        ("main_llm_processor", "CMD:START test_comp2"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(llm_command_daemon, "DB_FULL_PATH", str(db))
    monkeypatch.setattr(llm_command_daemon, "POLL_INTERVAL", 0)

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(llm_command_daemon.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        llm_command_daemon.main_loop("TEST")

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "SELECT desired_state FROM autorun_components WHERE component_id='test_comp2'"
    )
    row = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) FROM autorun_components WHERE component_id='test_comp2'"
    )
    count = cur.fetchone()[0]
    conn.close()

    assert row is not None and row[0] == "active"
    assert count == 1
