from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .storage import Storage


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "mock",
        "baseUrl": "https://api.openai.com/v1",
        "apiKeyEnv": "AUTORESEARCH_LLM_API_KEY",
        "defaultModel": "gpt-4.1",
        "profiles": {},
    }
}


def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        os.environ.setdefault(key, value)


def ensure_default_config(root: Path) -> Path:
    storage = Storage(root)
    storage.init()
    path = storage.meta / "config.json"
    if not path.exists():
        storage.write_json_atomic(path, DEFAULT_CONFIG)
    return path


def load_config(root: Path) -> dict[str, Any]:
    load_dotenv(root)
    path = Storage(root).meta / "config.json"
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    return _deep_merge(DEFAULT_CONFIG, loaded)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
