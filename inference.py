#!/usr/bin/env python3
"""
Inference abstraction layer for n0m1_agi.

All components import from here — never directly from llama_cpp or transformers.
To swap backends or models, UPDATE the inference_config table in the DB.
No component code needs to change.

CPU (mini PC):   n_gpu_layers=0  (default)
GPU (elsewhere): n_gpu_layers=-1 (full offload)
"""
import os
import sqlite3

DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
CONFIG_TABLE = 'inference_config'

# Singleton — model loaded once per process, reused for every infer() call
_model = None
_config = None


def _load_config() -> dict:
    """Read active inference config from DB."""
    conn = sqlite3.connect(DB_FULL_PATH)
    cur = conn.cursor()
    cur.execute(
        f"SELECT backend, model_path, n_gpu_layers, n_ctx, n_threads, "
        f"temperature, max_tokens FROM {CONFIG_TABLE} WHERE active=1 LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise RuntimeError(
            f"No active row in '{CONFIG_TABLE}'. Run init_database.py to populate."
        )
    return {
        'backend':      row[0],
        'model_path':   os.path.expanduser(row[1]),
        'n_gpu_layers': row[2],
        'n_ctx':        row[3],
        'n_threads':    row[4],
        'temperature':  row[5],
        'max_tokens':   row[6],
    }


def _get_model():
    """Return (model, config), loading on first call."""
    global _model, _config
    if _model is None:
        _config = _load_config()
        backend = _config['backend']

        if backend == 'llama_cpp':
            try:
                from llama_cpp import Llama
            except ImportError:
                raise ImportError(
                    "llama-cpp-python is not installed.\n"
                    "CPU:  pip install llama-cpp-python\n"
                    "CUDA: CMAKE_ARGS=\"-DGGML_CUDA=on\" pip install llama-cpp-python"
                )
            print(f"[inference] Loading model: {_config['model_path']}")
            print(f"[inference] n_gpu_layers={_config['n_gpu_layers']}  "
                  f"n_ctx={_config['n_ctx']}  threads={_config['n_threads']}")
            _model = Llama(
                model_path=_config['model_path'],
                n_gpu_layers=_config['n_gpu_layers'],
                n_ctx=_config['n_ctx'],
                n_threads=_config['n_threads'],
                verbose=False,
            )
            print("[inference] Model ready.")

        else:
            raise RuntimeError(
                f"Unknown backend '{backend}'. "
                "Supported: 'llama_cpp'"
            )

    return _model, _config


def infer(user_prompt: str, system_prompt: str = "", max_tokens: int = None) -> str:
    """
    Run inference and return the generated text.

    Args:
        user_prompt:   The data or question for the model.
        system_prompt: Role/behaviour instructions for the model.
        max_tokens:    Override DB default if set.

    Returns:
        Generated string, stripped of leading/trailing whitespace.
    """
    model, config = _get_model()
    max_tok = max_tokens if max_tokens is not None else config['max_tokens']

    if config['backend'] == 'llama_cpp':
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        output = model.create_chat_completion(
            messages=messages,
            max_tokens=max_tok,
            temperature=config['temperature'],
        )
        return output['choices'][0]['message']['content'].strip()

    raise RuntimeError(f"Unknown backend: {config['backend']}")


def reload():
    """Force model reload on next infer() call (e.g. after config change)."""
    global _model, _config
    _model = None
    _config = None
    print("[inference] Model unloaded — will reload on next infer() call.")
