#!/usr/bin/env python3
"""Daemon that logs CPU usage percent to the database."""
import time
import os
import sqlite3
import argparse

try:
    import psutil
except ImportError:
    psutil = None

from manager_utils import log_lifecycle_event

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
TABLE_NAME = 'cpu_usage_log'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
COMPONENT_ID = 'cpu_usage_daemon'
POLLING_INTERVAL_SECONDS = 10


def read_cpu_usage():
    if psutil:
        try:
            return psutil.cpu_percent(interval=None)
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
        'CPU usage daemon started',
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
        usage = read_cpu_usage()
        cursor.execute(
            f"INSERT INTO {TABLE_NAME} (cpu_usage) VALUES (?)",
            (usage,),
        )
        conn.commit()
        print(f"[{COMPONENT_ID}] CPU usage {usage}%")
        time.sleep(POLLING_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description='CPU usage daemon')
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
