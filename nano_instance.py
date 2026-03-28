#!/usr/bin/env python3
"""Nano instance component.

Reads sensor data from the DB, calls the shared inference layer, and writes
summaries back to the DB. All communication is through the DB backbone.

Inference backend is controlled entirely by the inference_config table —
no code changes needed to swap models or move to GPU.
"""
import argparse
import os
import time
import sqlite3
from collections import deque

from inference import infer
from manager_utils import log_lifecycle_event, log_db_access

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
METRICS_TABLE = 'system_metrics_log'
SUMMARY_TABLE = 'nano_outputs'
PROMPTS_TABLE = 'nano_prompts'
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
COMPONENT_ID_PREFIX = 'nano_instance'
# --- End Configuration ---


def announce_startup(component_id: str, run_type: str):
    log_lifecycle_event(
        DB_FULL_PATH,
        LIFECYCLE_TABLE_NAME,
        component_id,
        os.getpid(),
        'STARTED_SUCCESSFULLY',
        run_type,
        'Nano instance started',
        os.path.basename(__file__),
        os.path.abspath(__file__),
    )


def fetch_recent_metrics(conn: sqlite3.Connection, table: str, limit: int = 1):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    metric_col = None
    for c in ("temperature_celsius", "cpu_usage", "mem_usage", "cpu_temp"):
        if c in cols:
            metric_col = c
            break
    if not metric_col:
        raise ValueError(f"Unknown metric column in {table}")
    query = (
        f"SELECT timestamp, {metric_col} FROM {table} ORDER BY timestamp DESC LIMIT ?"
    )
    cur.execute(query, (limit,))
    rows = cur.fetchall()
    conn.commit()
    return rows, metric_col


def summarize_metrics_with_llm(context_window: deque, metric_col: str, system_prompt: str) -> str:
    """
    Pass recent metric readings to the LLM and return its summary.
    The system prompt (from nano_prompts table) defines how the nano
    should interpret and report on the data.
    """
    readings = list(context_window)
    if not readings:
        return None

    # Format readings as a clean data string for the model
    data_str = ", ".join(
        f"{r[1]}" for r in readings if r[1] is not None
    )
    unit = "C" if metric_col in {"temperature_celsius", "cpu_temp"} else "%"
    user_prompt = f"Recent {metric_col} readings ({unit}): [{data_str}]"

    try:
        result = infer(user_prompt, system_prompt=system_prompt)
        return result if result else None
    except Exception as e:
        print(f"[nano] Inference error: {e}")
        return None


def load_prompt(conn: sqlite3.Connection, nano_id: str):
    cur = conn.cursor()
    cur.execute(
        f"SELECT prompt, needs_reload FROM {PROMPTS_TABLE} WHERE nano_id=?",
        (nano_id,),
    )
    row = cur.fetchone()
    log_db_access(DB_FULL_PATH, f"{COMPONENT_ID_PREFIX}_{nano_id}", PROMPTS_TABLE, "READ")
    if not row:
        return None, False
    prompt, needs_reload = row
    return prompt, bool(needs_reload)


def mark_prompt_reloaded(conn: sqlite3.Connection, nano_id: str):
    cur = conn.cursor()
    cur.execute(
        f"UPDATE {PROMPTS_TABLE} SET needs_reload=0, modified_timestamp=CURRENT_TIMESTAMP WHERE nano_id=?",
        (nano_id,),
    )
    conn.commit()
    log_db_access(DB_FULL_PATH, f"{COMPONENT_ID_PREFIX}_{nano_id}", PROMPTS_TABLE, "WRITE")


def main():
    parser = argparse.ArgumentParser(description='Nano instance')
    parser.add_argument('--instance_id', default='default', help='Instance identifier')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    parser.add_argument('--db_path', default=DB_FULL_PATH, help='Path to metrics database')
    parser.add_argument('--metrics_table', default=METRICS_TABLE, help='Table to read metrics from')
    parser.add_argument('--summary_table', default=SUMMARY_TABLE, help='Table to store summaries')
    parser.add_argument('--pull_interval', type=int, default=5, help='Seconds between metric pulls')
    parser.add_argument('--infer_every', type=int, default=6,
                        help='Run inference every N metric pulls (default: 6 = once per 30s at 5s interval)')
    parser.add_argument('--context_window', type=int, default=10, help='Number of metric entries to keep in context')
    args = parser.parse_args()

    component_id = f"{COMPONENT_ID_PREFIX}_{args.instance_id}"

    announce_startup(component_id, args.run_type)

    conn = sqlite3.connect(args.db_path)

    prompt, needs_reload = load_prompt(conn, args.instance_id)
    if needs_reload:
        mark_prompt_reloaded(conn, args.instance_id)

    context = deque(maxlen=args.context_window)
    poll_count = 0

    print(f"[nano:{args.instance_id}] Running — inference every {args.infer_every} polls")
    try:
        while True:
            # Reload prompt from DB if flagged
            db_prompt, reload_flag = load_prompt(conn, args.instance_id)
            if db_prompt is not None and db_prompt != prompt:
                prompt = db_prompt
                print(f"[nano:{args.instance_id}] Prompt updated from DB")
            if reload_flag:
                mark_prompt_reloaded(conn, args.instance_id)

            # Read latest metric
            rows, metric_col = fetch_recent_metrics(conn, args.metrics_table, limit=1)
            log_db_access(DB_FULL_PATH, component_id, args.metrics_table, "READ")
            if rows:
                context.append(rows[0])
                print(f"[nano:{args.instance_id}] {metric_col}={rows[0][1]}")

            poll_count += 1

            # Run inference on the accumulated context window
            if poll_count >= args.infer_every and len(context) > 0:
                poll_count = 0
                system_prompt = prompt or (
                    f"You are a sensor monitor. Summarise the {metric_col} readings "
                    f"in one short sentence. Flag anything unusual."
                )
                summary = summarize_metrics_with_llm(context, metric_col, system_prompt)
                if summary:
                    print(f"[nano:{args.instance_id}] Summary: {summary}")
                    conn.execute(
                        f"INSERT INTO {args.summary_table} (nano_id, content) VALUES (?, ?)",
                        (args.instance_id, summary),
                    )
                    conn.commit()
                    log_db_access(DB_FULL_PATH, component_id, args.summary_table, "WRITE")

            time.sleep(args.pull_interval)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
