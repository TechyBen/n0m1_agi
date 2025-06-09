#!/usr/bin/env python3
import time
import sqlite3
import subprocess
import re
import struct
import os
import datetime
import argparse # New import

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
RAW_DATA_TABLE_NAME = 'cpu_temperature_log' # Where it logs temp data
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log' # For startup announcement
COMPONENT_ID = 'temp_main' # Its unique identifier

SMC_COMMAND = 'smc'
SMC_KEY = 'Th0D'
POLLING_INTERVAL_SECONDS = 10 # Or your preferred interval
# --- End Configuration ---

HEX_RE = re.compile(r'\(bytes ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2})\)')

def create_temp_data_table_if_not_exists(): # Renamed for clarity
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {RAW_DATA_TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                temperature_celsius REAL NOT NULL
            );
        """)
        conn.commit()
    except sqlite3.Error as e:
        print(f"[{COMPONENT_ID}] DB Error creating {RAW_DATA_TABLE_NAME}: {e}")
        raise
    finally:
        if conn:
            conn.close()

def annce_startup(run_type_arg):
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        process_pid = os.getpid()
        script_full_path = os.path.abspath(__file__)
        message = "Component initialized and starting main loop."

        cursor.execute(f"""
            INSERT INTO {LIFECYCLE_TABLE_NAME} 
            (component_id, process_pid, event_type, run_type, message, script_path) 
            VALUES (?, ?, 'STARTED_SUCCESSFULLY', ?, ?, ?)
        """, (COMPONENT_ID, process_pid, run_type_arg, message, script_full_path))
        conn.commit()
        print(f"[{COMPONENT_ID}] Successfully annced startup (PID: {process_pid}, RunType: {run_type_arg}) to {LIFECYCLE_TABLE_NAME}.")
    except sqlite3.Error as e:
        print(f"[{COMPONENT_ID}] DB Error anncing startup to {LIFECYCLE_TABLE_NAME}: {e}")
        # Decide if you want to raise this or just log and continue
    except Exception as e:
        print(f"[{COMPONENT_ID}] Unexpected error anncing startup: {e}")
    finally:
        if conn:
            conn.close()


def get_smc_cpu_temp():
    # This function remains the same as your working version from response #67
    # ... (your existing get_smc_cpu_temp logic) ...
    try:
        out = subprocess.check_output([SMC_COMMAND,'-k',SMC_KEY,'-r'], text=True, timeout=5)
        m = HEX_RE.search(out)
        if not m:
            raise ValueError(f"Could not parse hex bytes from smc output: {out!r}")
        b0, b1, b2, b3 = [int(x,16) for x in m.groups()]
        raw = bytes([b0, b1, b2, b3])
        temperature = struct.unpack('<f', raw)[0]
        return temperature
    except FileNotFoundError:
        print(f"[{COMPONENT_ID}] Error: Command '{SMC_COMMAND}' not found.")
        raise
    except subprocess.TimeoutExpired:
        print(f"[{COMPONENT_ID}] Error: Command '{SMC_COMMAND}' timed out.")
        raise
    except Exception as e:
        print(f"[{COMPONENT_ID}] Error during SMC read/decode for key {SMC_KEY}: {e}")
        raise

def main_loop(run_type_arg): # Pass run_type for context if needed, though not used in loop itself
    print(f"[{COMPONENT_ID}] Starting main loop. Polling every {POLLING_INTERVAL_SECONDS}s. Run Type: {run_type_arg}")
    conn = None # Initialize connection variable outside loop
    try:
        while True:
            temp = None
            try:
                temp = get_smc_cpu_temp()
                
                if conn is None: # Re-establish connection if lost (e.g., after an error)
                   conn = sqlite3.connect(DB_FULL_PATH)
                
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO {RAW_DATA_TABLE_NAME} (temperature_celsius) VALUES (?)",
                    (temp,)
                )
                conn.commit()
                print(f"[{COMPONENT_ID} - {datetime.datetime.now().strftime('%H:%M:%S')}] Logged {SMC_KEY} = {temp:.1f}°C")

            except Exception as e:
                print(f"[{COMPONENT_ID} - {datetime.datetime.now().strftime('%H:%M:%S')}] Error in loop: {e}")
                if conn: # If there was an error, close the connection; it will be reopened
                    try:
                        conn.close()
                    except: pass
                    conn = None 
                # Optional: log this loop error to component_lifecycle_log as well
            
            time.sleep(POLLING_INTERVAL_SECONDS)
    finally: # Ensure connection is closed on graceful exit (e.g. Ctrl+C if main is wrapped)
        if conn:
            conn.close()
            print(f"[{COMPONENT_ID}] Database connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{COMPONENT_ID} - CPU Temperature Daemon for n0m1_agi.")
    parser.add_argument('--run_type', type=str, default="MANUAL_RUN", 
                        help="Type of run (e.g., PRIMARY_RUN, TEST_ATTEMPT, MANUAL_RUN)")
    args = parser.parse_args()

    print(f"--- Starting {COMPONENT_ID} Daemon ---")
    try:
        create_temp_data_table_if_not_exists() # Its own data table
        announce_startup(args.run_type)      # Announce to lifecycle log
        main_loop(args.run_type)             # Start the main processing
    except KeyboardInterrupt:
        print(f"\n{COMPONENT_ID} Daemon stopped by user.")
        # Optionally log 'STOPPED_BY_USER' to component_lifecycle_log here
    except Exception as e:
        print(f"\nAn critical error occurred in {COMPONENT_ID} Daemon: {e}")
        # Optionally log 'CRITICAL_ERROR' to component_lifecycle_log here
