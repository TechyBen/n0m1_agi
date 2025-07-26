import os
import sqlite3
import sys
import pytest

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import nano_instance


def setup_test_db(tmp_path):
    db_path = tmp_path / "nano.db"
    conn = sqlite3.connect(db_path)
    # component_lifecycle_log required for log_lifecycle_event
    conn.execute(
        """CREATE TABLE component_lifecycle_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            component_id TEXT NOT NULL,
            process_pid INTEGER,
            event_type TEXT NOT NULL,
            run_type TEXT,
            message TEXT,
            manager_script TEXT,
            script_path TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE cpu_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            cpu_usage REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE cpu_usage_summary (
            id INTEGER PRIMARY KEY,
            nano_id TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            content TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE nano_prompts (
            nano_id TEXT PRIMARY KEY,
            prompt TEXT NOT NULL,
            modified_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            needs_reload INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        "INSERT INTO cpu_usage_log (cpu_usage) VALUES (95)"
    )
    conn.execute(
        "INSERT INTO nano_prompts (nano_id, prompt, needs_reload) VALUES ('default', 'test', 1)"
    )
    conn.commit()
    conn.close()
    return str(db_path)


def test_nano_instance_writes_output(tmp_path, monkeypatch):
    db_path = setup_test_db(tmp_path)

    monkeypatch.setattr(nano_instance, "DB_FULL_PATH", db_path)

    # speed up loop
    monkeypatch.setattr(nano_instance, "METRICS_TABLE", "cpu_usage_log")
    monkeypatch.setattr(nano_instance, "SUMMARY_TABLE", "cpu_usage_summary")

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(nano_instance.time, "sleep", fake_sleep)
    monkeypatch.setattr(sys, "argv", ["nano_instance.py"])

    with pytest.raises(StopIteration):
        nano_instance.main()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM cpu_usage_summary")
    count = cur.fetchone()[0]
    conn.close()

    assert count >= 1


def test_nano_instance_loads_prompt_and_clears_flag(tmp_path, monkeypatch):
    db_path = setup_test_db(tmp_path)

    monkeypatch.setattr(nano_instance, "DB_FULL_PATH", db_path)
    monkeypatch.setattr(nano_instance, "METRICS_TABLE", "cpu_usage_log")
    monkeypatch.setattr(nano_instance, "SUMMARY_TABLE", "cpu_usage_summary")

    def fake_sleep(_):
        raise StopIteration

    monkeypatch.setattr(nano_instance.time, "sleep", fake_sleep)
    monkeypatch.setattr(sys, "argv", ["nano_instance.py"])

    with pytest.raises(StopIteration):
        nano_instance.main()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT needs_reload FROM nano_prompts WHERE nano_id='default'")
    flag = cur.fetchone()[0]
    conn.close()

    assert flag == 0
