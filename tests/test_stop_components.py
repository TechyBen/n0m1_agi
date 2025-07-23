import os
import sys
import sqlite3
import subprocess
import time

import pytest

# Add project root to path so managers can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import nano_manager
import main_llm_manager


def _setup_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE component_lifecycle_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " event_timestamp TEXT, component_id TEXT, process_pid INTEGER, event_type TEXT,"
        " run_type TEXT, message TEXT, manager_script TEXT, script_path TEXT)"
    )
    conn.commit()
    conn.close()


def _launch_dummy_process():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


@pytest.mark.parametrize("module", [nano_manager, main_llm_manager])
def test_stop_component_terminates_process(tmp_path, module):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    db_path = tmp_path / "test.db"
    _setup_db(str(db_path))

    module.PID_DIR = str(pid_dir)
    module.DB_FULL_PATH = str(db_path)

    proc = _launch_dummy_process()
    pid_file = pid_dir / f"dummy.pid"
    pid_file.write_text(str(proc.pid))

    try:
        module.stop_component("dummy")
        # allow some time for process to terminate and reap it
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.poll() is not None

