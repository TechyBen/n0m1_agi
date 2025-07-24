#!/usr/bin/env python3
import subprocess
import os
import sys
import time
import argparse
import signal
import sqlite3
import json
from manager_utils import (
    get_venv_python,
    read_pid_file,
    remove_pid_file,
    stop_component_with_timeout,
    log_lifecycle_event,
    log_db_access,
)

# --- Configuration ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON_PATH = get_venv_python(PROJECT_DIR)
PID_DIR = os.path.join(PROJECT_DIR, 'pids')
LOGS_DIR = os.path.join(PROJECT_DIR, 'logs')

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
AUTORUN_TABLE_NAME = 'autorun_components'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
MANAGER_ID = 'nano_manager' # This manager's identifier for affinity
# --- End Configuration ---

# --- Utility Functions (get_pid_file_path, is_process_running, create_supporting_tables_if_not_exist) ---
# These should be identical to those in daemon_manager.py
# For brevity, I'll assume they are copied here.
def get_pid_file_path(component_id):
    return os.path.join(PID_DIR, f"{component_id}.pid")

def is_process_running(pid):
    if pid is None: return False
    try: os.kill(pid, 0)
    except OSError: return False
    else: return True

def create_supporting_tables_if_not_exist():
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {LIFECYCLE_TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                component_id TEXT NOT NULL, process_pid INTEGER, event_type TEXT NOT NULL,
                run_type TEXT, message TEXT, manager_script TEXT, script_path TEXT);
        """)
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_cll_component_id ON {LIFECYCLE_TABLE_NAME} (component_id);")
        # ... other indexes for lifecycle_log ...
        conn.commit()
    except sqlite3.Error as e: print(f"[{MANAGER_ID}] DB Error creating {LIFECYCLE_TABLE_NAME}: {e}")
    finally:
        if conn: conn.close()
# --- End Utility Functions ---


def start_component(component_id, base_script_name, launch_args_list, run_type):
    pid_file = get_pid_file_path(component_id)
    script_path = os.path.join(PROJECT_DIR, base_script_name)

    if os.path.exists(pid_file):
        pid = None
        try:
            with open(pid_file, 'r') as f: pid_str = f.read().strip(); pid = int(pid_str) if pid_str else None
            if pid and is_process_running(pid):
                # print(f"[{MANAGER_ID}] Component '{component_id}' (PID: {pid}) already running.") # Can be verbose
                return True # Indicate it's already running
        except: pass # Ignore errors, will attempt to start if check fails

    if not os.path.exists(script_path):
        print(f"[{MANAGER_ID}] ERROR: Script '{base_script_name}' for '{component_id}' not found. Skipping.")
        return False

    conn_lc = None
    try:
        conn_lc = sqlite3.connect(DB_FULL_PATH)
        cursor_lc = conn_lc.cursor()
        cursor_lc.execute(f"INSERT INTO {LIFECYCLE_TABLE_NAME} (component_id, event_type, run_type, message, manager_script) VALUES (?, 'START_ATTEMPT', ?, ?, ?)",
                          (component_id, run_type, f"Manager {MANAGER_ID} attempting to start {base_script_name}", os.path.basename(__file__)))
        conn_lc.commit()
    except sqlite3.Error as e: print(f"[{MANAGER_ID}] DB Error logging START_ATTEMPT for '{component_id}': {e}")
    finally:
        if conn_lc: conn_lc.close()

    full_command = [VENV_PYTHON_PATH, script_path] + launch_args_list + ['--run_type', run_type]
    command_str_for_log = " ".join(full_command)
    print(f"[{MANAGER_ID}] Starting component '{component_id}' with command: {command_str_for_log}...")
    
    log_file_path = os.path.join(LOGS_DIR, f"{component_id}.log")
    err_file_path = os.path.join(LOGS_DIR, f"{component_id}.err")
    os.makedirs(LOGS_DIR, exist_ok=True)

    try:
        process = subprocess.Popen(
            full_command, cwd=PROJECT_DIR,
            stdout=open(log_file_path, "a"), stderr=open(err_file_path, "a")
        )
        with open(pid_file, 'w') as f: f.write(str(process.pid))
        print(f"[{MANAGER_ID}] Component '{component_id}' started with PID: {process.pid}. Logs: '{log_file_path}'")
        return True
    except Exception as e:
        print(f"[{MANAGER_ID}] Error starting component '{component_id}': {e}")
        return False

def stop_component(component_id, signal_to_send=signal.SIGTERM):
    """Stop a running nano component by PID file."""
    pid_file = get_pid_file_path(component_id)
    pid = read_pid_file(pid_file)

    if not pid or not is_process_running(pid):
        log_lifecycle_event(
            DB_FULL_PATH,
            LIFECYCLE_TABLE_NAME,
            component_id,
            pid,
            'STOPPED_SUCCESSFULLY',
            None,
            'Already stopped',
            MANAGER_ID,
        )
        remove_pid_file(pid_file)
        print(f"[{MANAGER_ID}] Component '{component_id}' already stopped.")
        return True

    result = stop_component_with_timeout(
        component_id,
        pid,
        MANAGER_ID,
        LIFECYCLE_TABLE_NAME,
        DB_FULL_PATH,
        timeout_seconds=10,
        signal_to_send=signal_to_send,
    )

    if result:
        remove_pid_file(pid_file)
    return result


def get_component_status(component_id):
    # This function should be identical to get_daemon_status in daemon_manager.py
    # (or get_service_status from system_manager.py, adapted for component_id)
    # For brevity, assuming it's copied and works.
    # print(f"[{MANAGER_ID}] Placeholder: get_component_status({component_id}) called.")
    pid_file = get_pid_file_path(component_id)
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f: pid_str = f.read().strip(); pid = int(pid_str) if pid_str else None
            if pid and is_process_running(pid): return "RUNNING", pid
        except: pass
    return "STOPPED", None


def ensure_autorun_components_active():
    print(f"[{MANAGER_ID}] Ensuring autorun nano instances are active...")
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT component_id, base_script_name, launch_args_json, run_type_on_boot
            FROM {AUTORUN_TABLE_NAME}
            WHERE manager_affinity = ? AND desired_state = 'active'
        """, (MANAGER_ID,)) # MANAGER_ID is 'nano_manager'
        log_db_access(DB_FULL_PATH, MANAGER_ID, AUTORUN_TABLE_NAME, "READ")
        
        components_to_manage = cursor.fetchall()
        if not components_to_manage:
            print(f"[{MANAGER_ID}] No active nano instances found assigned to this manager in '{AUTORUN_TABLE_NAME}'.")
            return

        for comp_id, base_script, args_json_str, run_type in components_to_manage:
            status, _ = get_component_status(comp_id)
            if status == "RUNNING":
                continue # Already running
            
            print(f"[{MANAGER_ID}] Autorun component '{comp_id}' found inactive. Attempting to start.")
            launch_args = []
            if args_json_str and args_json_str.strip() != '{}':
                try:
                    args_dict = json.loads(args_json_str)
                    # Example: converts '{"--instance_id": "temp_analyzer_exp"}'
                    # to ['--instance_id', 'temp_analyzer_exp']
                    for key, value in args_dict.items():
                        launch_args.extend([key, str(value)])
                except json.JSONDecodeError:
                    print(f"[{MANAGER_ID}] WARNING: Could not parse launch_args_json for '{comp_id}': {args_json_str}")
            
            start_component(comp_id, base_script, launch_args, run_type)
            time.sleep(1) # Stagger starts of multiple nanos slightly

    except sqlite3.Error as e:
        print(f"[{MANAGER_ID}] Database Error in ensure_autorun_components_active: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    if not os.path.exists(VENV_PYTHON_PATH):
        print(f"ERROR: Python interpreter not found at '{VENV_PYTHON_PATH}'. Exiting.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description=f"{MANAGER_ID.replace('_',' ').title()} for n0m1_agi.")
    # Simplified CLI for managers that are primarily auto-run by boot_system.py
    # We can expand this later if direct CLI control for individual nanos via nano_manager is needed often.
    args_action = sys.argv[1] if len(sys.argv) > 1 else 'autorun' # Default to autorun

    for dir_path_to_check in [PID_DIR, LOGS_DIR]:
        if not os.path.exists(dir_path_to_check): os.makedirs(dir_path_to_check)
    create_supporting_tables_if_not_exist()

    if args_action == 'autorun':
        ensure_autorun_components_active()
    # Add elif for 'start', 'stop', 'status' specific component_id later if needed,
    # similar to daemon_manager.py's more complex argparse.
    # For now, boot_system.py launches it, and it does its autorun check.
    else:
        print(f"[{MANAGER_ID}] Action '{args_action}' understood as directive to ensure autorun components. For specific start/stop, use dedicated CLI options (to be expanded).")
        ensure_autorun_components_active() # Defaulting to ensure state

    print(f"[{MANAGER_ID}] Operations cycle complete.")
