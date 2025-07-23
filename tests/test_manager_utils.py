import os
import tempfile
import subprocess
import sys
import time

import pytest

# Ensure the project root is on sys.path so manager_utils can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from manager_utils import (
    write_pid_file,
    read_pid_file,
    is_process_running,
    get_venv_python,
)


def test_write_and_read_pid_file_roundtrip(tmp_path):
    pid_file = tmp_path / "test.pid"
    pid = 12345
    assert write_pid_file(str(pid_file), pid)
    assert read_pid_file(str(pid_file)) == pid


def test_read_pid_file_invalid(tmp_path):
    pid_file = tmp_path / "invalid.pid"
    pid_file.write_text("notanumber")
    assert read_pid_file(str(pid_file)) is None
    assert read_pid_file(str(pid_file.with_suffix('.missing')) ) is None


def test_is_process_running_for_running_and_stopped_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        assert is_process_running(proc.pid)
    finally:
        proc.terminate()
        proc.wait()
    # After process termination, it should report not running
    assert not is_process_running(proc.pid)


def test_get_venv_python_native_env(monkeypatch, tmp_path):
    monkeypatch.setenv("N0M1_NATIVE", "1")
    path = get_venv_python(str(tmp_path))
    assert path == sys.executable
    monkeypatch.delenv("N0M1_NATIVE", raising=False)

