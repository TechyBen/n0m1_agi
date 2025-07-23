#!/usr/bin/env python3
"""
Shared utility functions for all manager scripts.
This module provides common functionality for process management.
"""
import os
import signal
import time
import sqlite3
import sys
from typing import Optional, Tuple


def get_venv_python(project_dir: str) -> str:
    """Return path to the virtual environment Python interpreter.

    If the environment variable ``N0M1_NATIVE`` is set to ``1`` or ``true``,
    the path to the running Python interpreter (``sys.executable``) is
    returned instead. This allows the managers to run natively on systems
    without a dedicated virtual environment.
    """
    if os.environ.get("N0M1_NATIVE", "").lower() in {"1", "true", "yes"}:
        return sys.executable

    if os.name == "nt":
        return os.path.join(project_dir, "venv", "Scripts", "python.exe")
    return os.path.join(project_dir, "venv", "bin", "python")

def get_pid_file_path(pid_dir: str, component_id: str) -> str:
    """Get the PID file path for a component."""
    return os.path.join(pid_dir, f"{component_id}.pid")

def is_process_running(pid: Optional[int]) -> bool:
    """Check if a process with given PID is running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def read_pid_file(pid_file: str) -> Optional[int]:
    """Read PID from file, returning None if file doesn't exist or is invalid."""
    if not os.path.exists(pid_file):
        return None
    
    try:
        with open(pid_file, 'r') as f:
            pid_str = f.read().strip()
            return int(pid_str) if pid_str else None
    except (ValueError, IOError):
        return None

def write_pid_file(pid_file: str, pid: int) -> bool:
    """Write PID to file. Returns True on success."""
    try:
        with open(pid_file, 'w') as f:
            f.write(str(pid))
        return True
    except IOError as e:
        print(f"Error writing PID file {pid_file}: {e}")
        return False

def remove_pid_file(pid_file: str) -> bool:
    """Remove PID file if it exists. Returns True if removed or didn't exist."""
    if os.path.exists(pid_file):
        try:
            os.remove(pid_file)
            return True
        except OSError as e:
            print(f"Error removing PID file {pid_file}: {e}")
            return False
    return True

def stop_component_with_timeout(
    component_id: str,
    pid: int,
    manager_id: str,
    lifecycle_table: str,
    db_path: str,
    timeout_seconds: int = 10,
    signal_to_send: int = signal.SIGTERM
) -> bool:
    """
    Stop a component process with timeout and proper logging.
    Returns True if successfully stopped, False otherwise.
    """
    # Log stop request
    log_lifecycle_event(
        db_path, lifecycle_table, component_id, pid,
        'STOP_REQUESTED', None, f"{manager_id} requesting stop", manager_id
    )
    
    try:
        # Send initial signal
        os.kill(pid, signal_to_send)
        print(f"[{manager_id}] Sent {signal.Signals(signal_to_send).name} to {component_id} (PID: {pid})")
        
        # Wait for process to terminate
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if not is_process_running(pid):
                # Process stopped successfully
                log_lifecycle_event(
                    db_path, lifecycle_table, component_id, pid,
                    'STOPPED_SUCCESSFULLY', None, "Process terminated gracefully", manager_id
                )
                print(f"[{manager_id}] Component '{component_id}' stopped successfully.")
                return True
            time.sleep(0.1)
        
        # Timeout reached, try SIGKILL
        print(f"[{manager_id}] Component '{component_id}' didn't stop gracefully. Sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
        
        if not is_process_running(pid):
            log_lifecycle_event(
                db_path, lifecycle_table, component_id, pid,
                'STOPPED_FORCEFULLY', None, "Process killed with SIGKILL", manager_id
            )
            print(f"[{manager_id}] Component '{component_id}' forcefully stopped.")
            return True
        else:
            log_lifecycle_event(
                db_path, lifecycle_table, component_id, pid,
                'STOP_FAILED', None, "Failed to stop process", manager_id
            )
            print(f"[{manager_id}] ERROR: Failed to stop component '{component_id}'")
            return False
            
    except ProcessLookupError:
        # Process already dead
        print(f"[{manager_id}] Component '{component_id}' (PID: {pid}) already stopped.")
        return True
    except PermissionError:
        print(f"[{manager_id}] ERROR: Permission denied to stop '{component_id}' (PID: {pid})")
        return False
    except Exception as e:
        print(f"[{manager_id}] ERROR stopping '{component_id}': {e}")
        return False

def log_lifecycle_event(
    db_path: str,
    table_name: str,
    component_id: str,
    process_pid: Optional[int],
    event_type: str,
    run_type: Optional[str],
    message: str,
    manager_script: str,
    script_path: Optional[str] = None
) -> bool:
    """Log an event to the component_lifecycle_log table."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO {table_name}
            (component_id, process_pid, event_type, run_type, message, manager_script, script_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (component_id, process_pid, event_type, run_type, message, manager_script, script_path))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error logging lifecycle event: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_component_full_status(pid_file: str, component_id: str) -> Tuple[str, Optional[int]]:
    """
    Get component status with PID.
    Returns tuple of (status, pid) where status is 'RUNNING', 'STOPPED', or 'STALE_PID'
    """
    pid = read_pid_file(pid_file)
    
    if pid is None:
        return "STOPPED", None
    
    if is_process_running(pid):
        return "RUNNING", pid
    else:
        return "STALE_PID", pid

def ensure_db_connection(db_path: str, table_name: str) -> bool:
    """Test database connection and table existence."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
        return True
    except sqlite3.Error:
        return False
    finally:
        if conn:
            conn.close()

def create_required_directories(*directories: str) -> bool:
    """Create required directories if they don't exist."""
    try:
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
        return True
    except OSError as e:
        print(f"Error creating directories: {e}")
        return False
