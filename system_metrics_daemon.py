#!/usr/bin/env python3
"""Cross-platform system metrics daemon."""
import time
import os
import sqlite3
import argparse
import platform

try:
    import psutil
except ImportError:
    psutil = None

from manager_utils import log_lifecycle_event

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
TABLE_NAME = 'system_metrics_log'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
COMPONENT_ID = 'system_metrics_daemon'
POLLING_INTERVAL_SECONDS = 10
# --- End Configuration ---

# Import macOS temperature helper if available
if platform.system() == 'Darwin':
    try:
        from temp_main_daemon import get_smc_cpu_temp
    except Exception:
        get_smc_cpu_temp = None
else:
    get_smc_cpu_temp = None


def read_cpu_temp():
    """Return CPU temperature if available."""
    if platform.system() == 'Darwin' and get_smc_cpu_temp:
        try:
            return get_smc_cpu_temp()
        except Exception:
            return None

    if psutil and hasattr(psutil, 'sensors_temperatures'):
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None
            # look for common keys
            for key in ('coretemp', 'cpu-thermal', 'k10temp'):
                if key in temps and temps[key]:
                    return temps[key][0].current
            # fallback to first entry
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            return None
    return None


def read_cpu_usage():
    if psutil:
        try:
            return psutil.cpu_percent(interval=None)
        except Exception:
            return None
    return None


def read_mem_usage():
    if psutil:
        try:
            return psutil.virtual_memory().percent
        except Exception:
            return None
    return None


def log_start(run_type):
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE_NAME,
        COMPONENT_ID,
        os.getpid(),
        'STARTED_SUCCESSFULLY',
        run_type,
        'System metrics daemon started',
        COMPONENT_ID,
        os.path.abspath(__file__),
    )


def log_stop(run_type, message):
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE_NAME,
        COMPONENT_ID,
        os.getpid(),
        'STOPPED',
        run_type,
        message,
        COMPONENT_ID,
        os.path.abspath(__file__),
    )


def main_loop(run_type):
    conn = sqlite3.connect(DB_FULL_PATH)
    cursor = conn.cursor()
    while True:
        temp = read_cpu_temp()
        cpu = read_cpu_usage()
        mem = read_mem_usage()
        cursor.execute(
            f"INSERT INTO {TABLE_NAME} (cpu_temp, cpu_usage, mem_usage) VALUES (?, ?, ?)",
            (temp, cpu, mem),
        )
        conn.commit()
        print(f"[{COMPONENT_ID}] CPU {cpu}% MEM {mem}% TEMP {temp}")
        time.sleep(POLLING_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description='System metrics daemon')
    parser.add_argument('--run_type', type=str, default='MANUAL_RUN')
    args = parser.parse_args()

    log_start(args.run_type)
    try:
        main_loop(args.run_type)
    except KeyboardInterrupt:
        log_stop(args.run_type, 'Stopped via KeyboardInterrupt')
    except Exception as e:
        log_stop(args.run_type, f'Unexpected error: {e}')
    finally:
        pass


if __name__ == '__main__':
    main()
