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

from manager_utils import log_lifecycle_event

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
METRICS_TABLE = 'system_metrics_log'
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
    query = (
        f"SELECT timestamp, cpu_temp, cpu_usage, mem_usage "
        f"FROM {table} ORDER BY timestamp DESC LIMIT ?"
    )
    cur.execute(query, (limit,))
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description='Nano instance')
    parser.add_argument('--instance_id', default='default', help='Instance identifier')
    parser.add_argument('--model', default='sshleifer/tiny-gpt2', help='Model name or path')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    parser.add_argument('--db_path', default=DB_FULL_PATH, help='Path to metrics database')
    parser.add_argument('--metrics_table', default=METRICS_TABLE, help='Table to read metrics from')
    parser.add_argument('--pull_interval', type=int, default=5, help='Seconds between metric pulls')
    parser.add_argument('--lora', dest='lora_path', help='Optional LoRA weights path')
    parser.add_argument('--system_prompt', help='Path to system prompt text file')
    parser.add_argument('--context_window', type=int, default=10, help='Number of metric entries to keep in context')
    args = parser.parse_args()

    component_id = f"{COMPONENT_ID_PREFIX}_{args.instance_id}"

    announce_startup(component_id, args.run_type)

    model, tokenizer = load_model(args.model, args.lora_path)

    prompt = None
    if args.system_prompt:
        try:
            with open(args.system_prompt, "r") as f:
                prompt = f.read()
        except Exception as e:
            print(f"[nano] Failed to read system prompt: {e}")

    conn = sqlite3.connect(args.db_path)
    context = deque(maxlen=args.context_window)

    print(f"[nano:{args.instance_id}] Running idle loop")
    try:
        while True:
            rows = fetch_recent_metrics(conn, args.metrics_table, limit=1)
            if rows:
                context.append(rows[0])
                print(f"[nano:{args.instance_id}] Latest metrics: {rows[0]}")
            time.sleep(args.pull_interval)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
