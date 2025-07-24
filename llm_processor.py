#!/usr/bin/env python3
"""Main LLM processing component.

This script loads a language model using the HuggingFace transformers library
and idles waiting for tasks. It records lifecycle events in the SQLite database
so managers can monitor its status.
"""
import argparse
import os
import time
import sqlite3

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError:
    AutoModelForCausalLM = None
    AutoTokenizer = None

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


def load_model(model_name: str):
    """Load a language model. If transformers is missing, log a warning."""
    if AutoModelForCausalLM is None:
        print(f"[{COMPONENT_ID}] transformers not available, skipping model load")
        return None, None
    print(f"[{COMPONENT_ID}] Loading model '{model_name}' ...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
    print(f"[{COMPONENT_ID}] Model loaded")
    return model, tokenizer


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


def main():
    parser = argparse.ArgumentParser(description='Main LLM Processor')
    parser.add_argument('--model', default='distilgpt2', help='HuggingFace model name or path')
    parser.add_argument('--run_type', default='MANUAL_RUN', help='Run type reported to lifecycle log')
    parser.add_argument('--threads', type=int, default=4, help='Thread count hint for model')
    args = parser.parse_args()

    announce_startup(args.run_type)
    load_model(args.model)

    conn = sqlite3.connect(DB_FULL_PATH)
    read_tables, output_table = load_config(conn)

    print(f"[{COMPONENT_ID}] Entering idle loop")
    try:
        while True:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, notification_type, payload FROM {NOTIFY_TABLE} WHERE llm_id=? AND processed=0",
                (COMPONENT_ID,),
            )
            note = cur.fetchone()
            log_db_access(DB_FULL_PATH, COMPONENT_ID, NOTIFY_TABLE, "READ")
            if note:
                note_id, note_type, payload = note
                cur.execute(f"UPDATE {NOTIFY_TABLE} SET processed=1 WHERE id=?", (note_id,))
                conn.commit()
                if note_type == 'CONFIG_RELOAD':
                    read_tables, output_table = load_config(conn)
                elif note_type in {'RUN', 'PUSH'}:
                    tables = read_tables
                    if payload:
                        tables = [t.strip() for t in payload.split(',') if t.strip()]
                    for table in tables:
                        try:
                            cur.execute(f"SELECT COUNT(*) FROM {table}")
                            count = cur.fetchone()[0]
                            log_db_access(DB_FULL_PATH, COMPONENT_ID, table, "READ")
                        except sqlite3.Error:
                            count = 0
                        cur.execute(
                            f"INSERT INTO {output_table} (llm_id, content) VALUES (?, ?)",
                            (COMPONENT_ID, f'{table} rows={count}'),
                        )
                    conn.commit()
                elif note_type == 'PULL_REQUEST' and payload:
                    cur.execute(
                        f"INSERT INTO {output_table} (llm_id, content) VALUES (?, ?)",
                        (COMPONENT_ID, f'REQUEST:{payload}'),
                    )
                    conn.commit()

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
