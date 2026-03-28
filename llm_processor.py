#!/usr/bin/env python3
"""Main LLM processing component.

Polls llm_notifications for tasks, reads nano summaries from the DB,
calls the shared inference layer, and writes results to llm_outputs.
All communication is through the DB backbone.

Inference backend is controlled entirely by the inference_config table.
"""
import argparse
import os
import time
import sqlite3

from inference import infer
from manager_utils import log_lifecycle_event, log_db_access

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
COMPONENT_ID = 'main_llm_processor'
CONFIG_TABLE = 'llm_io_config'
NOTIFY_TABLE = 'llm_notifications'
POLL_INTERVAL = 5
# --- End Configuration ---


def announce_startup(run_type: str):
    """Record startup event in lifecycle log."""
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE_NAME,
        COMPONENT_ID,
        os.getpid(),
        'STARTED_SUCCESSFULLY',
        run_type,
        'LLM processor started',
        os.path.basename(__file__),
        os.path.abspath(__file__),
    )

def load_config(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute(
        f"SELECT read_tables, output_table FROM {CONFIG_TABLE} WHERE llm_id=?",
        (COMPONENT_ID,),
    )
    row = cur.fetchone()
    log_db_access(DB_FULL_PATH, COMPONENT_ID, CONFIG_TABLE, "READ")
    if row:
        read_tables = [t.strip() for t in row[0].split(',') if t.strip()]
        output_table = row[1]
    else:
        read_tables = []
        output_table = None
    return read_tables, output_table


def gather_summaries(conn: sqlite3.Connection, tables: list) -> str:
    """Read the latest row from each summary table and format as context."""
    cur = conn.cursor()
    parts = []
    for table in tables:
        try:
            cur.execute(
                f"SELECT content FROM {table} ORDER BY timestamp DESC LIMIT 1"
            )
            row = cur.fetchone()
            log_db_access(DB_FULL_PATH, COMPONENT_ID, table, "READ")
            if row and row[0]:
                parts.append(f"[{table}] {row[0]}")
        except sqlite3.Error:
            pass
    return "\n".join(parts) if parts else "No nano summaries available yet."


def main():
    parser = argparse.ArgumentParser(description='Main LLM Processor')
    parser.add_argument('--run_type', default='MANUAL_RUN', help='Run type reported to lifecycle log')
    args = parser.parse_args()

    announce_startup(args.run_type)

    conn = sqlite3.connect(DB_FULL_PATH)
    read_tables, output_table = load_config(conn)

    system_prompt = (
        "You are the central intelligence of the n0m1_agi system. "
        "Nano sensors report system metrics to you. "
        "Analyse the provided summaries, identify anything notable, "
        "and respond concisely in one or two sentences."
    )

    print(f"[{COMPONENT_ID}] Entering idle loop — polling {NOTIFY_TABLE} every {POLL_INTERVAL}s")
    try:
        while True:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, notification_type, payload FROM {NOTIFY_TABLE} "
                f"WHERE llm_id=? AND processed=0 ORDER BY id ASC LIMIT 1",
                (COMPONENT_ID,),
            )
            note = cur.fetchone()
            log_db_access(DB_FULL_PATH, COMPONENT_ID, NOTIFY_TABLE, "READ")

            if note:
                note_id, note_type, payload = note
                cur.execute(
                    f"UPDATE {NOTIFY_TABLE} SET processed=1 WHERE id=?", (note_id,)
                )
                conn.commit()

                if note_type == 'CONFIG_RELOAD':
                    read_tables, output_table = load_config(conn)
                    print(f"[{COMPONENT_ID}] Config reloaded — reading: {read_tables}")

                elif note_type in {'RUN', 'PUSH'}:
                    tables = read_tables
                    if payload:
                        tables = [t.strip() for t in payload.split(',') if t.strip()]

                    context = gather_summaries(conn, tables)
                    print(f"[{COMPONENT_ID}] Running inference on nano summaries...")
                    try:
                        response = infer(context, system_prompt=system_prompt)
                        print(f"[{COMPONENT_ID}] Response: {response}")
                        cur.execute(
                            f"INSERT INTO {output_table} (llm_id, content) VALUES (?, ?)",
                            (COMPONENT_ID, response),
                        )
                        conn.commit()
                        log_db_access(DB_FULL_PATH, COMPONENT_ID, output_table, "WRITE")
                    except Exception as e:
                        print(f"[{COMPONENT_ID}] Inference error: {e}")

                elif note_type == 'PULL_REQUEST' and payload:
                    print(f"[{COMPONENT_ID}] Pull request: {payload}")
                    try:
                        response = infer(payload, system_prompt=system_prompt)
                        cur.execute(
                            f"INSERT INTO {output_table} (llm_id, content) VALUES (?, ?)",
                            (COMPONENT_ID, response),
                        )
                        conn.commit()
                        log_db_access(DB_FULL_PATH, COMPONENT_ID, output_table, "WRITE")
                    except Exception as e:
                        print(f"[{COMPONENT_ID}] Inference error: {e}")

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
