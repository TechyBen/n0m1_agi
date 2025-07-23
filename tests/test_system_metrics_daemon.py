import os
import sqlite3
import sys
import types
import importlib

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import system_metrics_daemon


def setup_test_db(tmp_path):
    db_path = tmp_path / "metrics.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE system_metrics_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            cpu_temp REAL,
            cpu_usage REAL NOT NULL,
            mem_usage REAL NOT NULL
        )"""
    )
    conn.commit()
    conn.close()
    return str(db_path)


def test_system_metrics_daemon_writes_rows(tmp_path, monkeypatch):
    db_path = setup_test_db(tmp_path)

    # Point daemon to temp database and speed up polling
    monkeypatch.setattr(system_metrics_daemon, "DB_FULL_PATH", db_path)
    monkeypatch.setattr(system_metrics_daemon, "POLLING_INTERVAL_SECONDS", 0)

    # Mock psutil or its functions for deterministic results
    if system_metrics_daemon.psutil is None:
        dummy = types.SimpleNamespace(
            cpu_percent=lambda interval=None: 1.0,
            virtual_memory=lambda: types.SimpleNamespace(percent=2.0),
            sensors_temperatures=lambda: {
                "coretemp": [types.SimpleNamespace(current=3.0)]
            },
        )
        monkeypatch.setattr(system_metrics_daemon, "psutil", dummy, raising=False)
    else:
        monkeypatch.setattr(system_metrics_daemon.psutil, "cpu_percent", lambda interval=None: 1.0)
        monkeypatch.setattr(system_metrics_daemon.psutil, "virtual_memory", lambda: types.SimpleNamespace(percent=2.0))
        monkeypatch.setattr(
            system_metrics_daemon.psutil,
            "sensors_temperatures",
            lambda: {"coretemp": [types.SimpleNamespace(current=3.0)]},
        )

    call = {"count": 0}

    def fake_sleep(_):
        call["count"] += 1
        if call["count"] >= 2:
            raise StopIteration

    monkeypatch.setattr(system_metrics_daemon.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        system_metrics_daemon.main_loop("TEST")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM system_metrics_log")
    count = cur.fetchone()[0]
    conn.close()

    assert count >= 1
