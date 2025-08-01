#!/usr/bin/env python3
import time
import sqlite3
import subprocess
import re
import struct
import os
import datetime
import argparse
import shutil

try:
    import psutil
except ImportError:  # psutil may not be installed
    psutil = None

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
RAW_DATA_TABLE_NAME = 'cpu_temperature_log'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
# Use the same component_id that init_database.py inserts into the autorun
# configuration so lifecycle logs align correctly.
COMPONENT_ID = 'temp_main_daemon'

SMC_COMMAND = 'smc'
SMC_KEY = 'Th0D'
POLLING_INTERVAL_SECONDS = 10
# --- End Configuration ---

HEX_RE = re.compile(r'\(bytes ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2})\)')

def create_temp_data_table_if_not_exists():
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

def announce_startup(run_type_arg):  # Fixed function name
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
        print(f"[{COMPONENT_ID}] Successfully announced startup (PID: {process_pid}, RunType: {run_type_arg}) to {LIFECYCLE_TABLE_NAME}.")
    except sqlite3.Error as e:
        print(f"[{COMPONENT_ID}] DB Error announcing startup to {LIFECYCLE_TABLE_NAME}: {e}")
    except Exception as e:
        print(f"[{COMPONENT_ID}] Unexpected error announcing startup: {e}")
    finally:
        if conn:
            conn.close()

def get_cpu_temp():
    """Return the current CPU temperature in Celsius or None if unavailable."""
    # First try psutil if available
    if psutil is not None:
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
            if temps:
                for entries in temps.values():
                    for entry in entries:
                        current = getattr(entry, "current", None)
                        if current is not None:
                            return float(current)
            else:
                print(f"[{COMPONENT_ID}] psutil returned no temperature data.")
        except Exception as e:
            print(f"[{COMPONENT_ID}] psutil error retrieving temperature: {e}")

    # Fallback to macOS 'smc' command if available
    if shutil.which(SMC_COMMAND):
        try:
            out = subprocess.check_output([SMC_COMMAND, '-k', SMC_KEY, '-r'], text=True, timeout=5)
            m = HEX_RE.search(out)
            if m:
                b0, b1, b2, b3 = [int(x, 16) for x in m.groups()]
                raw = bytes([b0, b1, b2, b3])
                return struct.unpack('<f', raw)[0]
        except Exception as e:
            print(f"[{COMPONENT_ID}] Fallback SMC read failed: {e}")

    return None

def main_loop(run_type_arg):
    print(f"[{COMPONENT_ID}] Starting main loop. Polling every {POLLING_INTERVAL_SECONDS}s. Run Type: {run_type_arg}")
    conn = None
    try:
        while True:
            temp = None
            try:
                temp = get_cpu_temp()

                if temp is not None:
                    if conn is None:
                       conn = sqlite3.connect(DB_FULL_PATH)

                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO {RAW_DATA_TABLE_NAME} (temperature_celsius) VALUES (?)",
                        (temp,)
                    )
                    conn.commit()
                    print(f"[{COMPONENT_ID} - {datetime.datetime.now().strftime('%H:%M:%S')}] Logged CPU temp = {temp:.1f}°C")
                else:
                    print(f"[{COMPONENT_ID} - {datetime.datetime.now().strftime('%H:%M:%S')}] Temperature data unavailable.")

            except Exception as e:
                print(f"[{COMPONENT_ID} - {datetime.datetime.now().strftime('%H:%M:%S')}] Error in loop: {e}")
                if conn:
                    try:
                        conn.close()
                    except: pass
                    conn = None 
            
            time.sleep(POLLING_INTERVAL_SECONDS)
    finally:
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
        create_temp_data_table_if_not_exists()
        announce_startup(args.run_type)  # Fixed function call
        main_loop(args.run_type)
    except KeyboardInterrupt:
        print(f"\n{COMPONENT_ID} Daemon stopped by user.")
    except Exception as e:
        print(f"\nA critical error occurred in {COMPONENT_ID} Daemon: {e}")
