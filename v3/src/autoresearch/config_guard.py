from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigContractError(ValueError):
    pass


def validate_protected_config_paths(
    baseline_root: Path,
    workspace_root: Path,
    protected_paths: dict[str, list[str]],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for relative_file, json_paths in protected_paths.items():
        baseline_file = baseline_root / relative_file
        workspace_file = workspace_root / relative_file
        if not baseline_file.exists() or not workspace_file.exists():
            violations.append(
                {
                    "file": relative_file,
                    "path": "<file>",
                    "expected": "present",
                    "actual": "missing",
                }
            )
            continue
        baseline = _read_json(baseline_file)
        current = _read_json(workspace_file)
        for path in json_paths:
            for expected_path, expected_value in _iter_values(baseline, path):
                actual_value = _get_path(current, expected_path)
                if actual_value != expected_value:
                    violations.append(
                        {
                            "file": relative_file,
                            "path": ".".join(expected_path),
                            "expected": expected_value,
                            "actual": actual_value,
                        }
                    )
    return violations


def raise_for_config_violations(violations: list[dict[str, Any]]) -> None:
    if not violations:
        return
    first = violations[0]
    raise ConfigContractError(
        "protected config field changed: "
        f"{first['file']}:{first['path']} expected {first['expected']!r} "
        f"got {first['actual']!r}"
    )


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _iter_values(data: Any, pattern: str) -> list[tuple[list[str], Any]]:
    parts = pattern.split(".") if pattern else []
    out: list[tuple[list[str], Any]] = []

    def walk(node: Any, rest: list[str], prefix: list[str]) -> None:
        if not rest:
            out.append((prefix, node))
            return
        head, *tail = rest
        if head == "*":
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, tail, [*prefix, str(key)])
            return
        if isinstance(node, dict) and head in node:
            walk(node[head], tail, [*prefix, head])

    walk(data, parts, [])
    return out


def _get_path(data: Any, path: list[str]) -> Any:
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
