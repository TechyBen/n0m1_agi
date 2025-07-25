#!/usr/bin/env python3
"""Daemon that processes commands produced by the LLM."""
import argparse
import os
import sqlite3
import time

from manager_utils import log_db_access

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
OUTPUT_TABLE = 'llm_outputs'
AUTORUN_TABLE = 'autorun_components'
COMPONENT_ID = 'llm_command_daemon'
POLL_INTERVAL = 5


def handle_command(conn: sqlite3.Connection, command: str) -> None:
    """Process a single command string."""
    if command.startswith('CMD:START '):
        comp_id = command[len('CMD:START '):].strip()
        cur = conn.cursor()
        cur.execute(
            f"SELECT 1 FROM {AUTORUN_TABLE} WHERE component_id=?",
            (comp_id,)
        )
        log_db_access(DB_FULL_PATH, COMPONENT_ID, AUTORUN_TABLE, 'READ')
        if cur.fetchone():
            cur.execute(
                f"UPDATE {AUTORUN_TABLE} SET desired_state='active' WHERE component_id=?",
                (comp_id,),
            )
        else:
            cur.execute(
                f"""INSERT INTO {AUTORUN_TABLE} (
                    component_id, base_script_name, manager_affinity, desired_state
                ) VALUES (?, ?, ?, 'active')""",
                (comp_id, f'{comp_id}.py', 'daemon_manager'),
            )
        log_db_access(DB_FULL_PATH, COMPONENT_ID, AUTORUN_TABLE, 'WRITE')
        conn.commit()


def main_loop(run_type: str) -> None:
    conn = sqlite3.connect(DB_FULL_PATH)
    cur = conn.cursor()
    last_id = 0
    while True:
        cur.execute(
            f"SELECT id, content FROM {OUTPUT_TABLE} WHERE id>? ORDER BY id",
            (last_id,),
        )
        rows = cur.fetchall()
        log_db_access(DB_FULL_PATH, COMPONENT_ID, OUTPUT_TABLE, 'READ')
        for row_id, content in rows:
            last_id = row_id
            if content and content.startswith('CMD:'):
                handle_command(conn, content)
                cur.execute(f"DELETE FROM {OUTPUT_TABLE} WHERE id=?", (row_id,))
                log_db_access(DB_FULL_PATH, COMPONENT_ID, OUTPUT_TABLE, 'WRITE')
                conn.commit()
        time.sleep(POLL_INTERVAL)


def main() -> None:
    parser = argparse.ArgumentParser(description='LLM command daemon')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    args = parser.parse_args()

    try:
        main_loop(args.run_type)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
