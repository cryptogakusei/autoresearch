from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from .descriptor import ExperimentDescriptor


class BenchmarkError(RuntimeError):
    pass


def run_benchmark(
    descriptor: ExperimentDescriptor,
    artifact_dir: Path,
    experiment_id: str,
    root: Path,
) -> dict[str, Any]:
    command = [
        part.format(
            artifact_dir=str(artifact_dir),
            experiment_id=experiment_id,
            experiment_type=descriptor.name,
            root=str(root),
        )
        for part in descriptor.benchmark.command
    ]

    from .remote import load_remote_config, run_remote_benchmark

    remote_config = load_remote_config()
    if remote_config is not None:
        stdout = run_remote_benchmark(
            config=remote_config,
            command=command,
            artifact_dir=artifact_dir,
            local_root=root,
            timeout=descriptor.benchmark.timeoutSec,
        )
    else:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            text=True,
            capture_output=True,
            timeout=descriptor.benchmark.timeoutSec,
        )
        if completed.returncode != 0:
            raise BenchmarkError(completed.stderr.strip() or completed.stdout.strip())
        stdout = completed.stdout

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"Benchmark did not emit valid JSON: {exc}") from exc

