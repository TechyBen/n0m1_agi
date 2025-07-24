#!/usr/bin/env python3
"""
Daemon Manager for n0m1_agi system.
This manager is responsible for starting, stopping, and monitoring all components
with the 'daemon_manager' affinity in the autorun_components table.
It is launched by the main boot system (boot_system_enhanced.py).
"""
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
    get_pid_file_path,
    is_process_running,
    read_pid_file,
    write_pid_file,
    remove_pid_file,
    log_lifecycle_event,
    log_db_access,
    create_required_directories,
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
MANAGER_ID = 'daemon_manager'  # This manager's identifier for affinity
# --- End Configuration ---

def start_component(component_id: str, base_script_name: str, launch_args_list: list, run_type: str):
    """
    Starts a single component using a subprocess, logs the attempt,
    and creates a PID file.
    """
    script_path = os.path.join(PROJECT_DIR, base_script_name)
    pid_file = get_pid_file_path(PID_DIR, component_id)

    # Check if script exists before attempting to run
    if not os.path.exists(script_path):
        log_lifecycle_event(
            DB_FULL_PATH, LIFECYCLE_TABLE_NAME, component_id, None,
            'START_FAILED', run_type, f"Script not found at {script_path}", MANAGER_ID, script_path
        )
        print(f"[{MANAGER_ID}] ERROR: Script '{base_script_name}' for component '{component_id}' not found. Skipping.")
        return False

    # Log the start attempt
    log_lifecycle_event(
        DB_FULL_PATH, LIFECYCLE_TABLE_NAME, component_id, None,
        'START_ATTEMPT', run_type, f"Manager '{MANAGER_ID}' attempting to start.", MANAGER_ID, script_path
    )

    # Construct the full command to execute
    full_command = [VENV_PYTHON_PATH, script_path] + launch_args_list + ['--run_type', run_type]
    print(f"[{MANAGER_ID}] Launching: {' '.join(full_command)}")

    log_file = os.path.join(LOGS_DIR, f"{component_id}.log")
    err_file = os.path.join(LOGS_DIR, f"{component_id}.err")

    try:
        # Launch the component as a new background process
        process = subprocess.Popen(
            full_command,
            cwd=PROJECT_DIR,
            stdout=open(log_file, 'a'),
            stderr=open(err_file, 'a'),
            preexec_fn=os.setsid  # Start in a new session to detach from manager
        )
        # Write the new PID to file
        write_pid_file(pid_file, process.pid)
        print(f"[{MANAGER_ID}] Component '{component_id}' started with PID: {process.pid}.")
        return True
    except Exception as e:
        log_lifecycle_event(
            DB_FULL_PATH, LIFECYCLE_TABLE_NAME, component_id, None,
            'START_FAILED', run_type, f"Error on Popen: {e}", MANAGER_ID, script_path
        )
        print(f"[{MANAGER_ID}] ERROR starting component '{component_id}': {e}")
        return False

def stop_component(component_id: str):
    """
    Stops a single component using its PID file.
    (This logic would now typically live in manager_utils.py)
    """
    pid_file = get_pid_file_path(PID_DIR, component_id)
    pid = read_pid_file(pid_file)

    if not is_process_running(pid):
        print(f"[{MANAGER_ID}] Component '{component_id}' is already stopped.")
        remove_pid_file(pid_file)
        return

    # Using a more robust stop utility that could be in manager_utils
    # For now, keeping it simple
    print(f"[{MANAGER_ID}] Sending SIGTERM to component '{component_id}' (PID: {pid})...")
    log_lifecycle_event(DB_FULL_PATH, LIFECYCLE_TABLE_NAME, component_id, pid, 'STOP_REQUESTED', None, "Manager sending SIGTERM", MANAGER_ID)
    os.kill(pid, signal.SIGTERM)
    # The boot system or a monitor would handle cleanup if it doesn't terminate.

def get_components_from_db():
    """Fetch all components this manager is responsible for."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT component_id, base_script_name, launch_args_json, run_type_on_boot, desired_state
            FROM {AUTORUN_TABLE_NAME}
            WHERE manager_affinity = ?
        """, (MANAGER_ID,))
        log_db_access(DB_FULL_PATH, MANAGER_ID, AUTORUN_TABLE_NAME, "READ")
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"[{MANAGER_ID}] FATAL: Database Error fetching component list: {e}")
        return []
    finally:
        if conn:
            conn.close()

def main():
    """Main operational loop for the Daemon Manager."""
    print(f"--- Starting {MANAGER_ID} ---")
    
    # Create required directories on startup
    create_required_directories(PID_DIR, LOGS_DIR)

    # Main loop to ensure desired state
    while True:
        try:
            components = get_components_from_db()
            if not components:
                print(f"[{MANAGER_ID}] No components configured for this manager. Sleeping...")
            
            for comp_id, base_script, args_json, run_type, desired_state in components:
                pid_file = get_pid_file_path(PID_DIR, comp_id)
                pid = read_pid_file(pid_file)
                running = is_process_running(pid)
                
                if desired_state == 'active' and not running:
                    print(f"[{MANAGER_ID}] Found stopped component that should be active: '{comp_id}'. Starting...")
                    launch_args = []
                    if args_json and args_json.strip() != '{}':
                        try:
                            args_dict = json.loads(args_json)
                            for key, value in args_dict.items():
                                launch_args.extend([key, str(value)])
                        except json.JSONDecodeError:
                            print(f"[{MANAGER_ID}] WARNING: Could not parse launch_args_json for '{comp_id}': {args_json}")
                    
                    start_component(comp_id, base_script, launch_args, run_type)

                elif desired_state == 'inactive' and running:
                    print(f"[{MANAGER_ID}] Found running component that should be inactive: '{comp_id}'. Stopping...")
                    stop_component(comp_id)

            # This manager will periodically check and enforce the desired state
            time.sleep(30) # Check every 30 seconds

        except KeyboardInterrupt:
            print(f"[{MANAGER_ID}] Shutdown requested by user.")
            break
        except Exception as e:
            print(f"[{MANAGER_ID}] An unexpected error occurred in the main loop: {e}")
            time.sleep(60) # Wait longer on error to prevent fast error loops

    print(f"--- {MANAGER_ID} shutting down. ---")

if __name__ == "__main__":
    if not os.path.exists(VENV_PYTHON_PATH):
        print(f"ERROR: Python interpreter not found at '{VENV_PYTHON_PATH}'. Exiting.")
        sys.exit(1)
    
    main()
