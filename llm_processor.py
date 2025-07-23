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

from manager_utils import log_lifecycle_event

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
LIFECYCLE_TABLE_NAME = 'component_lifecycle_log'
COMPONENT_ID = 'main_llm_processor'
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


def main():
    parser = argparse.ArgumentParser(description='Main LLM Processor')
    parser.add_argument('--model', default='distilgpt2', help='HuggingFace model name or path')
    parser.add_argument('--run_type', default='MANUAL_RUN', help='Run type reported to lifecycle log')
    parser.add_argument('--threads', type=int, default=4, help='Thread count hint for model')
    args = parser.parse_args()

    announce_startup(args.run_type)
    load_model(args.model)

    print(f"[{COMPONENT_ID}] Entering idle loop")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
