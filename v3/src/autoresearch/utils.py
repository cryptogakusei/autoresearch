from __future__ import annotations

from datetime import UTC, datetime
import re


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_element(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def normalize_elements(values: list[str]) -> list[str]:
    normalized = [normalize_element(v) for v in values]
    return sorted({v for v in normalized if v})


def clamp_text(value: str, limit: int = 2000) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."

