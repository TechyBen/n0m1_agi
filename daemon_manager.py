#!/usr/bin/env python3
import subprocess
import os
import sys
import time
import argparse
import signal
import sqlite3
import json # For parsing launch_args_json

# --- Configuration ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON_PATH = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python')
PID_DIR = os.path.join(PROJECT_DIR, 'pids')
LOGS_DIR = os.path.join(PROJECT_DIR, 'logs')

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
AUTORUN_TABLE_NAME = 'autorun_components'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log' # For logging start attempts
MANAGER_ID = 'daemon_manager' # This manager's identifier for affinity
# --- End Configuration ---

# --- Utility Functions (mostly from previous system_manager.py) ---
def get_pid_file_path(component_id): # Changed from daemon_name to generic component_id
    return os.path.join(PID_DIR, f"{component_id}.pid")

def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

def create_supporting_tables_if_not_exist(): # For lifecycle log
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {LIFECYCLE_TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                component_id TEXT NOT NULL,
                process_pid INTEGER,
                event_type TEXT NOT NULL,
                run_type TEXT,
                message TEXT,
                manager_script TEXT,
                script_path TEXT
            );
        """)
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_cll_component_id ON {LIFECYCLE_TABLE_NAME} (component_id);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_cll_event_type ON {LIFECYCLE_TABLE_NAME} (event_type);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_cll_event_timestamp ON {LIFECYCLE_TABLE_NAME} (event_timestamp);")
        conn.commit()
    except sqlite3.Error as e:
        print(f"[{MANAGER_ID}] DB Error creating {LIFECYCLE_TABLE_NAME}: {e}")
        # Decide if this is fatal for the manager
    finally:
        if conn:
            conn.close()

def start_component(component_id, base_script_name, launch_args_list, run_type):
    pid_file = get_pid_file_path(component_id)
    script_path = os.path.join(PROJECT_DIR, base_script_name)

    if not os.path.exists(script_path):
        print(f"[{MANAGER_ID}] ERROR: Script '{base_script_name}' for component '{component_id}' not found at '{script_path}'. Skipping.")
        return

    # Check if already running via PID file
    if os.path.exists(pid_file):
        pid = None
        try:
            with open(pid_file, 'r') as f: pid_str = f.read().strip(); pid = int(pid_str) if pid_str else None
            if pid and is_process_running(pid):
                print(f"[{MANAGER_ID}] Component '{component_id}' (PID: {pid}) is already running.")
                return
            else: # Stale PID file
                print(f"[{MANAGER_ID}] Stale PID file for '{component_id}' (PID: {pid if pid else 'unknown'}). Removing.")
                os.remove(pid_file)
        except (ValueError, FileNotFoundError): # Corrupt or missing PID file after check
            if os.path.exists(pid_file): os.remove(pid_file)
        except Exception as e:
            print(f"[{MANAGER_ID}] Error checking PID for '{component_id}': {e}")

    # Log start attempt
    conn_lc = None
    try:
        conn_lc = sqlite3.connect(DB_FULL_PATH)
        cursor_lc = conn_lc.cursor()
        cursor_lc.execute(f"""
            INSERT INTO {LIFECYCLE_TABLE_NAME}
            (component_id, event_type, run_type, message, manager_script)
            VALUES (?, 'START_ATTEMPT', ?, ?, ?)
        """, (component_id, run_type, f"Manager {MANAGER_ID} attempting to start {base_script_name}", os.path.basename(__file__)))
        conn_lc.commit()
    except sqlite3.Error as e:
        print(f"[{MANAGER_ID}] DB Error logging START_ATTEMPT for '{component_id}': {e}")
    finally:
        if conn_lc: conn_lc.close()

    # Construct full command
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
        print(f"[{MANAGER_ID}] Component '{component_id}' started with PID: {process.pid}. Logs: '{log_file_path}', '{err_file_path}'")
    except Exception as e:
        print(f"[{MANAGER_ID}] Error starting component '{component_id}': {e}")
        # Optionally log ERROR_ON_MANAGED_STARTUP to lifecycle_log

def stop_component(component_id, signal_to_send=signal.SIGTERM): # Generic stop
    # This function remains very similar to stop_daemon / stop_service from system_manager.py
    # It uses component_id to find the PID file, read PID, send signal, check, send SIGKILL if needed, remove PID.
    # Remember to log 'STOP_REQUESTED' and 'STOPPED_SUCCESSFULLY' or 'STOP_FAILED' to component_lifecycle_log
    pid_file = get_pid_file_path(component_id)
    if not os.path.exists(pid_file):
        print(f"[{MANAGER_ID}] Component '{component_id}' not running (no PID file).")
        return

    pid = None
    try: # Read PID
        with open(pid_file, 'r') as f: pid_str = f.read().strip(); pid = int(pid_str) if pid_str else None
    except Exception as e: print(f"Error reading PID for {component_id}: {e}"); os.remove(pid_file); return

    if not pid: print(f"Empty PID file for {component_id}. Removing."); os.remove(pid_file); return

    if is_process_running(pid):
        print(f"[{MANAGER_ID}] Stopping '{component_id}' (PID: {pid})...")
        # Log STOP_REQUESTED
        # os.kill(pid, signal_to_send) ... etc. ...
        # Log STOPPED_SUCCESSFULLY or FAILED
    else:
        print(f"[{MANAGER_ID}] Component '{component_id}' (PID: {pid}) not running (stale PID).")
    if os.path.exists(pid_file): os.remove(pid_file)


def get_component_status(component_id): # Generic status
    # Similar to get_daemon_status / get_service_status from system_manager.py
    # Uses component_id to check PID file and process status.
    # For brevity, assuming it's defined and works.
    pid_file = get_pid_file_path(component_id)
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f: pid_str = f.read().strip(); pid = int(pid_str) if pid_str else None
            if pid and is_process_running(pid):
                print(f"[{MANAGER_ID}] Component '{component_id}' is RUNNING (PID: {pid}).")
                return "RUNNING"
        except: pass # Fall through if issues
    print(f"[{MANAGER_ID}] Component '{component_id}' is STOPPED.")
    return "STOPPED"


def ensure_autorun_components_active():
    print(f"[{MANAGER_ID}] Ensuring autorun components are active...")
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT component_id, base_script_name, launch_args_json, run_type_on_boot 
            FROM {AUTORUN_TABLE_NAME} 
            WHERE manager_affinity = ? AND desired_state = 'active'
        """, (MANAGER_ID,)) # MANAGER_ID tells it which components to manage
        
        components_to_manage = cursor.fetchall()
        if not components_to_manage:
            print(f"[{MANAGER_ID}] No active components found assigned to this manager in '{AUTORUN_TABLE_NAME}'.")
            return

        for comp_id, base_script, args_json_str, run_type in components_to_manage:
            current_status = get_component_status(comp_id) # Check before starting
            if current_status == "RUNNING":
                # print(f"[{MANAGER_ID}] Component '{comp_id}' is already running.") # Can be verbose
                continue
            
            print(f"[{MANAGER_ID}] Autorun component '{comp_id}' found inactive. Attempting to start.")
            launch_args = []
            if args_json_str and args_json_str.strip() != '{}': # Handle empty JSON object
                try:
                    args_dict = json.loads(args_json_str)
                    for key, value in args_dict.items(): # Converts {"--arg": "val"} to ["--arg", "val"]
                        launch_args.extend([key, str(value)]) # Ensure value is string for command line
                except json.JSONDecodeError:
                    print(f"[{MANAGER_ID}] WARNING: Could not parse launch_args_json for '{comp_id}': {args_json_str}")
            
            start_component(comp_id, base_script, launch_args, run_type)
            time.sleep(0.5) # Stagger starts slightly

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
    parser.add_argument('action', nargs='?', default='autorun', 
                        choices=['start', 'stop', 'reset', 'status', 'autorun'],
                        help="Action: 'start', 'stop', 'reset', 'status' a specific component, or 'autorun' (default) to manage all assigned active components.")
    parser.add_argument('component_id', nargs='?', default=None,
                        help="Specific component_id to act upon (e.g., 'temp_main_daemon'). Required for start/stop/reset/status if not 'autorun'. Use 'all_managed' to target all components this manager is responsible for.")
    
    args = parser.parse_args()

    # Ensure supporting tables and directories exist
    for dir_path_to_check in [PID_DIR, LOGS_DIR]:
        if not os.path.exists(dir_path_to_check):
            os.makedirs(dir_path_to_check)
            print(f"Created directory: {dir_path_to_check}")
    create_supporting_tables_if_not_exist() # For component_lifecycle_log

    if args.action == 'autorun':
        ensure_autorun_components_active()
    else: # Specific actions like start, stop, status, reset
        components_to_act_on = []
        if args.component_id and args.component_id.lower() == 'all_managed':
            conn = sqlite3.connect(DB_FULL_PATH)
            cursor = conn.cursor()
            cursor.execute(f"SELECT component_id FROM {AUTORUN_TABLE_NAME} WHERE manager_affinity = ?", (MANAGER_ID,))
            components_to_act_on = [row[0] for row in cursor.fetchall()]
            conn.close()
            if not components_to_act_on:
                print(f"[{MANAGER_ID}] No components found assigned to this manager for 'all_managed' action.")
        elif args.component_id:
            # Verify this component_id is managed by this manager
            conn = sqlite3.connect(DB_FULL_PATH)
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM {AUTORUN_TABLE_NAME} WHERE component_id = ? AND manager_affinity = ?", (args.component_id, MANAGER_ID))
            if cursor.fetchone():
                components_to_act_on = [args.component_id]
            else:
                print(f"[{MANAGER_ID}] Error: Component '{args.component_id}' not found or not managed by {MANAGER_ID}.")
            conn.close()
        
        if not components_to_act_on and args.component_id: # If specific ID was given but not found/assigned
             print(f"[{MANAGER_ID}] Action '{args.action}' requires a valid component_id assigned to this manager, or use 'all_managed'.")
             sys.exit(1)
        elif not components_to_act_on and not args.component_id : # Action requires component_id but none given
            print(f"[{MANAGER_ID}] Action '{args.action}' requires a component_id or 'all_managed'.")
            sys.exit(1)


        for comp_id in components_to_act_on:
            if args.action == 'status':
                get_component_status(comp_id) # Re-adapted from system_manager.py
            elif args.action == 'stop':
                stop_component(comp_id) # Re-adapted from system_manager.py
            elif args.action == 'start': # For manual start via CLI
                 conn = sqlite3.connect(DB_FULL_PATH)
                 cursor = conn.cursor()
                 cursor.execute(f"SELECT base_script_name, launch_args_json, run_type_on_boot FROM {AUTORUN_TABLE_NAME} WHERE component_id = ?", (comp_id,))
                 cfg = cursor.fetchone()
                 conn.close()
                 if cfg:
                     base_script, args_json, run_type = cfg
                     launch_args = []
                     if args_json and args_json.strip() != '{}': try: args_dict = json.loads(args_json); launch_args = [item for k,v in args_dict.items() for item in (k,str(v))] except: pass
                     # For manual CLI start, maybe use a different run_type?
                     start_component(comp_id, base_script, launch_args, "MANUAL_CLI_START") 
                 else:
                     print(f"[{MANAGER_ID}] No config found for '{comp_id}' to perform manual start.")
            elif args.action == 'reset':
                print(f"[{MANAGER_ID}] Resetting component '{comp_id}'...")
                stop_component(comp_id)
                time.sleep(2)
                # Fetch config again for start, similar to 'start' action
                conn = sqlite3.connect(DB_FULL_PATH)
                cursor = conn.cursor()
                cursor.execute(f"SELECT base_script_name, launch_args_json, run_type_on_boot FROM {AUTORUN_TABLE_NAME} WHERE component_id = ?", (comp_id,))
                cfg = cursor.fetchone()
                conn.close()
                if cfg:
                    base_script, args_json, run_type = cfg
                    launch_args = []
                    if args_json and args_json.strip() != '{}': try: args_dict = json.loads(args_json); launch_args = [item for k,v in args_dict.items() for item in (k,str(v))] except: pass
                    start_component(comp_id, base_script, launch_args, "MANUAL_CLI_RESET") # Specific run_type
                else:
                    print(f"[{MANAGER_ID}] No config found for '{comp_id}' to perform reset start.")

    print(f"[{MANAGER_ID}] Operations cycle complete.")
