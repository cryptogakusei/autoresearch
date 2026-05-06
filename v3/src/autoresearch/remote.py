from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .benchmark import BenchmarkError


@dataclass(frozen=True)
class RemoteConfig:
    host: str
    user: str
    key_path: str
    port: int
    remote_root: str
    data_dir: str


def load_remote_config() -> RemoteConfig | None:
    host = os.environ.get("AUTORESEARCH_REMOTE_HOST")
    if not host:
        return None
    key_path = os.environ.get("AUTORESEARCH_REMOTE_KEY", "")
    if not key_path:
        raise RuntimeError("AUTORESEARCH_REMOTE_HOST is set but AUTORESEARCH_REMOTE_KEY is missing")
    key_path = str(Path(key_path).expanduser())
    return RemoteConfig(
        host=host,
        user=os.environ.get("AUTORESEARCH_REMOTE_USER", "ubuntu"),
        key_path=key_path,
        port=int(os.environ.get("AUTORESEARCH_REMOTE_PORT", "22")),
        remote_root=os.environ.get("AUTORESEARCH_REMOTE_ROOT", "/home/ubuntu/autoresearch"),
        data_dir=os.environ.get("AUTORESEARCH_REMOTE_DATA_DIR", "/home/ubuntu/data"),
    )


def _ssh_base(config: RemoteConfig) -> list[str]:
    return [
        "ssh",
        "-i", config.key_path,
        "-p", str(config.port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=5",
        "-o", "BatchMode=yes",
        f"{config.user}@{config.host}",
    ]


def ssh_run(config: RemoteConfig, command: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [*_ssh_base(config), command],
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError(f"remote command timed out after {timeout} seconds") from exc


def rsync_to(config: RemoteConfig, local_path: Path, remote_path: str) -> None:
    ssh_transport = f"ssh -i {config.key_path} -p {config.port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    try:
        result = subprocess.run(
            [
                "rsync", "-az", "--delete",
                "-e", ssh_transport,
                str(local_path) + "/",
                f"{config.user}@{config.host}:{remote_path}/",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError("rsync to remote timed out after 120 seconds") from exc
    if result.returncode != 0:
        raise BenchmarkError(f"rsync to remote failed: {result.stderr.strip()}")


def run_remote_benchmark(
    config: RemoteConfig,
    command: list[str],
    artifact_dir: Path,
    local_root: Path,
    experiment_id: str,
    timeout: int,
) -> str:
    relative_artifact = artifact_dir.relative_to(local_root)
    remote_artifact = f"{config.remote_root}/{relative_artifact}"
    safe_id = _safe_remote_name(experiment_id)
    log_dir = f"{config.remote_root}/.autoresearch/logs"
    stdout_file = f"{log_dir}/{safe_id}.stdout.json"
    log_file = f"{log_dir}/{safe_id}.log"

    ssh_run(config, f"mkdir -p {shlex.quote(remote_artifact)}", timeout=15)
    benchmarks_dir = local_root / "benchmarks"
    if benchmarks_dir.exists():
        rsync_to(config, benchmarks_dir, f"{config.remote_root}/benchmarks")
    rsync_to(config, artifact_dir, remote_artifact)

    remote_command = [
        part.replace(str(artifact_dir), remote_artifact)
            .replace(str(local_root), config.remote_root)
        for part in command
    ]
    started_line = f"started remote benchmark {experiment_id}"
    shell_cmd = (
        f"cd {shlex.quote(config.remote_root)} && "
        f"mkdir -p {shlex.quote(log_dir)} && "
        f"printf '%s\\n' {shlex.quote(started_line)} > {shlex.quote(log_file)} && "
        f"({shlex.join(remote_command)}) > {shlex.quote(stdout_file)} "
        f"2>> {shlex.quote(log_file)}; "
        "status=$?; "
        f"printf '%s\\n' \"exit_status=$status\" >> {shlex.quote(log_file)}; "
        f"cat {shlex.quote(stdout_file)}; "
        "exit $status"
    )

    try:
        result = ssh_run(config, shell_cmd, timeout)
    except BenchmarkError as exc:
        recovered = _recover_remote_json(config, stdout_file)
        if recovered is not None:
            return recovered
        raise BenchmarkError(f"{exc}; remote log: {log_file}") from exc
    if result.returncode != 0:
        recovered = _recover_remote_json(config, stdout_file)
        if recovered is not None:
            return recovered
        raise BenchmarkError(
            f"Remote benchmark failed (exit {result.returncode}); remote log: {log_file}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _safe_remote_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return safe or "benchmark"


def _recover_remote_json(config: RemoteConfig, remote_path: str) -> str | None:
    try:
        result = ssh_run(config, f"cat {shlex.quote(remote_path)}", timeout=30)
    except BenchmarkError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return result.stdout
