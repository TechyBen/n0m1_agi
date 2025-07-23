#!/usr/bin/env python3
"""Daemon that monitors LLM configuration changes.

This daemon watches the ``llm_io_config`` table for updates and writes
rows to ``llm_notifications`` when a reload is requested.
"""
import argparse
import os
import sqlite3
import time

from manager_utils import log_lifecycle_event

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
CONFIG_TABLE = 'llm_io_config'
NOTIFY_TABLE = 'llm_notifications'
LIFECYCLE_TABLE = 'component_lifecycle_log'
COMPONENT_ID = 'llm_config_daemon'
POLL_INTERVAL = 5


def log_start(run_type: str):
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE,
        COMPONENT_ID,
        os.getpid(),
        'STARTED_SUCCESSFULLY',
        run_type,
        'LLM config daemon started',
        COMPONENT_ID,
        os.path.abspath(__file__),
    )


def log_stop(run_type: str, message: str):
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE,
        COMPONENT_ID,
        os.getpid(),
        'STOPPED',
        run_type,
        message,
        COMPONENT_ID,
        os.path.abspath(__file__),
    )


def main_loop(run_type: str):
    conn = sqlite3.connect(DB_FULL_PATH)
    cur = conn.cursor()
    while True:
        cur.execute(
            f"SELECT needs_reload FROM {CONFIG_TABLE} WHERE llm_id=?",
            ('main_llm_processor',),
        )
        row = cur.fetchone()
        if row and row[0]:
            cur.execute(
                f"INSERT INTO {NOTIFY_TABLE} (llm_id, notification_type) VALUES (?, ?)",
                ('main_llm_processor', 'CONFIG_RELOAD'),
            )
            cur.execute(
                f"UPDATE {CONFIG_TABLE} SET needs_reload=0 WHERE llm_id=?",
                ('main_llm_processor',),
            )
            conn.commit()
        time.sleep(POLL_INTERVAL)


def main() -> None:
    parser = argparse.ArgumentParser(description='LLM configuration daemon')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    args = parser.parse_args()

    log_start(args.run_type)
    try:
        main_loop(args.run_type)
    except KeyboardInterrupt:
        log_stop(args.run_type, 'Stopped via KeyboardInterrupt')
    except Exception as e:  # pragma: no cover - unexpected errors
        log_stop(args.run_type, f'Unexpected error: {e}')


if __name__ == '__main__':
    main()
