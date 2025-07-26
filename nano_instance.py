#!/usr/bin/env python3
"""Nano instance component.

This script loads a small language model (e.g., nanoGPT) and runs an idle loop.
It logs lifecycle events so nano_manager can track its status.
"""
import argparse
import os
import time
import sqlite3
from collections import deque

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
except ImportError:
    AutoModelForCausalLM = None
    AutoTokenizer = None
    PeftModel = None

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


def load_model(model_name: str, lora_path: str = None):
    if AutoModelForCausalLM is None:
        print("[nano] transformers not available, skipping model load")
        return None, None
    print(f"[nano] Loading model '{model_name}' ...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
    if lora_path:
        if PeftModel is None:
            print("[nano] peft not installed, cannot load LoRA weights")
        else:
            try:
                model = PeftModel.from_pretrained(model, lora_path)
                print(f"[nano] LoRA weights loaded from {lora_path}")
            except Exception as e:
                print(f"[nano] Failed to load LoRA weights: {e}")
    print("[nano] Model loaded")
    return model, tokenizer


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


def summarize_metrics(entry, metric_col):
    """Return summary string if metrics indicate noteworthy event."""
    if not entry:
        return None
    ts, value = entry
    if metric_col in {"temperature_celsius", "cpu_temp"}:
        if value is not None and value > 80:
            return f"High CPU temperature detected: {value}C"
    elif metric_col == "cpu_usage":
        if value is not None and value > 90:
            return f"High CPU usage detected: {value}%"
    elif metric_col == "mem_usage":
        if value is not None and value > 90:
            return f"High memory usage detected: {value}%"
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
    parser.add_argument('--model', default='sshleifer/tiny-gpt2', help='Model name or path')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    parser.add_argument('--db_path', default=DB_FULL_PATH, help='Path to metrics database')
    parser.add_argument('--metrics_table', default=METRICS_TABLE, help='Table to read metrics from')
    parser.add_argument('--summary_table', default=SUMMARY_TABLE, help='Table to store summaries')
    parser.add_argument('--pull_interval', type=int, default=5, help='Seconds between metric pulls')
    parser.add_argument('--lora', dest='lora_path', help='Optional LoRA weights path')
    parser.add_argument('--system_prompt', help='Path to system prompt text file')
    parser.add_argument('--context_window', type=int, default=10, help='Number of metric entries to keep in context')
    args = parser.parse_args()

    component_id = f"{COMPONENT_ID_PREFIX}_{args.instance_id}"

    announce_startup(component_id, args.run_type)

    model, tokenizer = load_model(args.model, args.lora_path)

    conn = sqlite3.connect(args.db_path)

    prompt, needs_reload = load_prompt(conn, args.instance_id)
    if prompt is None and args.system_prompt:
        try:
            with open(args.system_prompt, "r") as f:
                prompt = f.read()
        except Exception as e:
            print(f"[nano] Failed to read system prompt: {e}")
    if needs_reload:
        mark_prompt_reloaded(conn, args.instance_id)

    context = deque(maxlen=args.context_window)

    print(f"[nano:{args.instance_id}] Running idle loop")
    try:
        while True:
            db_prompt, reload_flag = load_prompt(conn, args.instance_id)
            if db_prompt is not None:
                if db_prompt != prompt:
                    prompt = db_prompt
                if reload_flag:
                    mark_prompt_reloaded(conn, args.instance_id)

            rows, metric_col = fetch_recent_metrics(conn, args.metrics_table, limit=1)
            log_db_access(DB_FULL_PATH, f"{COMPONENT_ID_PREFIX}_{args.instance_id}", args.metrics_table, "READ")
            if rows:
                context.append(rows[0])
                latest = rows[0]
                print(f"[nano:{args.instance_id}] Latest metrics: {latest}")
                summary = summarize_metrics(latest, metric_col)
                if summary:
                    conn.execute(
                        f"INSERT INTO {args.summary_table} (nano_id, content) VALUES (?, ?)",
                        (args.instance_id, summary),
                    )
                    conn.commit()
            time.sleep(args.pull_interval)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
