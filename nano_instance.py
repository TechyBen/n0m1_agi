#!/usr/bin/env python3
"""Nano instance component.

This script loads a small language model (e.g., nanoGPT) and runs an idle loop.
It logs lifecycle events so nano_manager can track its status.
"""
import argparse
import os
import time

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
COMPONENT_ID_PREFIX = 'nano_instance'
# --- End Configuration ---


def load_model(model_name: str):
    if AutoModelForCausalLM is None:
        print(f"[nano] transformers not available, skipping model load")
        return None, None
    print(f"[nano] Loading model '{model_name}' ...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
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


def main():
    parser = argparse.ArgumentParser(description='Nano instance')
    parser.add_argument('--instance_id', default='default', help='Instance identifier')
    parser.add_argument('--model', default='sshleifer/tiny-gpt2', help='Model name or path')
    parser.add_argument('--run_type', default='MANUAL_RUN')
    args = parser.parse_args()

    component_id = f"{COMPONENT_ID_PREFIX}_{args.instance_id}"

    announce_startup(component_id, args.run_type)
    load_model(args.model)

    print(f"[nano:{args.instance_id}] Running idle loop")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
