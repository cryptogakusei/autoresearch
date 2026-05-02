from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
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
        data_dir=os.environ.get("AUTORESEARCH_REMOTE_DATA_DIR", "/home/ubuntu/data/darcy"),
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
    return subprocess.run(
        [*_ssh_base(config), command],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def rsync_to(config: RemoteConfig, local_path: Path, remote_path: str) -> None:
    ssh_transport = f"ssh -i {config.key_path} -p {config.port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
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
    if result.returncode != 0:
        raise BenchmarkError(f"rsync to remote failed: {result.stderr.strip()}")


def run_remote_benchmark(
    config: RemoteConfig,
    command: list[str],
    artifact_dir: Path,
    local_root: Path,
    timeout: int,
) -> str:
    relative_artifact = artifact_dir.relative_to(local_root)
    remote_artifact = f"{config.remote_root}/{relative_artifact}"

    ssh_run(config, f"mkdir -p {shlex.quote(remote_artifact)}", timeout=15)
    rsync_to(config, artifact_dir, remote_artifact)

    remote_command = [
        part.replace(str(artifact_dir), remote_artifact)
            .replace(str(local_root), config.remote_root)
        for part in command
    ]
    shell_cmd = f"cd {shlex.quote(config.remote_root)} && {shlex.join(remote_command)}"

    result = ssh_run(config, shell_cmd, timeout)
    if result.returncode != 0:
        raise BenchmarkError(
            f"Remote benchmark failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )
    return result.stdout
