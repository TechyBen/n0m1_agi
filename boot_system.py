#!/usr/bin/env python3
import subprocess
import os
import sys
import time

# --- Configuration ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__)) # Assumes boot_system.py is in nouse_agi
VENV_PYTHON_PATH = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python')

# Define the manager scripts to be launched
# We assume these manager scripts, when run, will start their respective components.
# For now, we're not passing specific 'start' commands to them,
# assuming they default to starting their services when executed.
# This can be refined later if managers need specific commands like 'start all'.
MANAGER_SCRIPTS = [
    'daemon_manager.py',
    'nano_manager.py',
    'main_llm_manager.py', # Renamed from main_manager.py for clarity
]

# Directory for logs from the manager scripts themselves (optional)
LOGS_DIR = os.path.join(PROJECT_DIR, "logs_managers") # Separate from nano/daemon logs
# --- End Configuration ---

def check_python_interpreter():
    if not os.path.exists(VENV_PYTHON_PATH):
        print(f"ERROR: Python interpreter not found at '{VENV_PYTHON_PATH}'.")
        print("Please ensure the virtual environment exists and the path is correct.")
        sys.exit(1)
    print(f"Using Python interpreter: {VENV_PYTHON_PATH}")

def launch_manager(manager_script_name):
    script_path = os.path.join(PROJECT_DIR, manager_script_name)
    if not os.path.exists(script_path):
        print(f"ERROR: Manager script '{manager_script_name}' not found at '{script_path}'. Skipping.")
        return None

    print(f"Attempting to launch '{manager_script_name}'...")
    try:
        # Create a dedicated log file for this manager's output
        manager_log_file = os.path.join(LOGS_DIR, f"{manager_script_name.replace('.py', '')}.log")
        manager_err_file = os.path.join(LOGS_DIR, f"{manager_script_name.replace('.py', '')}.err")

        with open(manager_log_file, 'a') as flog, open(manager_err_file, 'a') as ferr:
            process = subprocess.Popen(
                [VENV_PYTHON_PATH, script_path],
                cwd=PROJECT_DIR,
                stdout=flog,
                stderr=ferr
            )
        print(f"Launched '{manager_script_name}' with PID: {process.pid}. Output logged to '{LOGS_DIR}'.")
        return process
    except Exception as e:
        print(f"ERROR launching '{manager_script_name}': {e}")
        return None

if __name__ == "__main__":
    print("--- Starting nouse_agi System ---")
    check_python_interpreter()

    if not os.path.exists(LOGS_DIR):
        try:
            os.makedirs(LOGS_DIR)
            print(f"Created manager logs directory: {LOGS_DIR}")
        except OSError as e:
            print(f"Error creating manager logs directory '{LOGS_DIR}': {e}. Please create it manually.")
            sys.exit(1)
            
    launched_managers = {}
    for script_name in MANAGER_SCRIPTS:
        p = launch_manager(script_name)
        if p:
            launched_managers[script_name] = p
        time.sleep(1) # Small delay between launching managers

    if launched_managers:
        print("\n--- All configured managers launched. ---")
        print("System components should now be starting up via their respective managers.")
        print("Check individual logs in '~/nouse_agi/logs/' (for daemons/nanos) and '~/nouse_agi/logs_managers/' (for managers).")
        print("This boot script will now exit. Manager processes will continue running in the background.")
    else:
        print("\n--- No managers were successfully launched. Please check errors. ---")