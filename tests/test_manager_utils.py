import os
import tempfile
import subprocess
import sys
import time
import platform
import types
import signal

import pytest

# Ensure the project root is on sys.path so manager_utils can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import manager_utils

from manager_utils import (
    write_pid_file,
    read_pid_file,
    is_process_running,
    get_venv_python,
    launch_subprocess,
    terminate_process,
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


def test_launch_subprocess_posix(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        class P:
            pid = 123
        return P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    fake_os = types.SimpleNamespace(name="posix", setsid=os.setsid)
    monkeypatch.setattr(manager_utils, "os", fake_os)
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    launch_subprocess(["echo"])
    assert captured.get("preexec_fn") == os.setsid
    assert "creationflags" not in captured


def test_launch_subprocess_windows(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        class P:
            pid = 123
        return P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    fake_os = types.SimpleNamespace(name="nt")
    monkeypatch.setattr(manager_utils, "os", fake_os)
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    launch_subprocess(["echo"])
    assert captured.get("creationflags") == subprocess.CREATE_NEW_PROCESS_GROUP
    assert "preexec_fn" not in captured


def test_terminate_process_posix(monkeypatch):
    calls = {}

    def fake_killpg(pgid, sig):
        calls["pgid"] = pgid
        calls["sig"] = sig

    fake_os = types.SimpleNamespace(name="posix", killpg=fake_killpg, getpgid=lambda pid: 42)
    monkeypatch.setattr(manager_utils, "os", fake_os)

    proc = types.SimpleNamespace(pid=1)
    terminate_process(proc)
    assert calls["pgid"] == 42
    assert calls["sig"] == signal.SIGTERM


def test_terminate_process_windows(monkeypatch):
    signals = []

    class P:
        def __init__(self):
            self.pid = 1

        def send_signal(self, sig):
            signals.append(sig)

        def terminate(self):
            signals.append("TERM")

    fake_os = types.SimpleNamespace(name="nt")
    monkeypatch.setattr(manager_utils, "os", fake_os)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 0, raising=False)
    p = P()
    terminate_process(p)
    assert signals == [signal.CTRL_BREAK_EVENT]

