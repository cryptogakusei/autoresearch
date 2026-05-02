from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any

from .models import IdeaTree, RunState
from .utils import now_iso


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.meta = root / ".autoresearch"
        self.state_dir = self.meta / "state"
        self.artifacts_dir = self.meta / "artifacts"
        self.logs_dir = self.meta / "logs"
        self.references_dir = self.meta / "references"
        self.workspace_dir = root / "workspace" / "current"

    def init(self) -> None:
        for path in [
            self.state_dir,
            self.artifacts_dir,
            self.logs_dir,
            self.references_dir,
            self.workspace_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def idea_tree_path(self, experiment_type: str) -> Path:
        return self.state_dir / f"{experiment_type}.ideas.json"

    def signals_path(self, experiment_type: str) -> Path:
        return self.state_dir / f"{experiment_type}.signals.json"

    def run_state_path(self) -> Path:
        return self.state_dir / "run.json"

    def load_tree(self, experiment_type: str) -> IdeaTree:
        path = self.idea_tree_path(experiment_type)
        if not path.exists():
            return IdeaTree.empty()
        return IdeaTree.from_json(self.read_json(path))

    def save_tree(self, experiment_type: str, tree: IdeaTree) -> None:
        self.write_json_atomic(self.idea_tree_path(experiment_type), tree.to_json())

    def load_run_state(self) -> RunState:
        path = self.run_state_path()
        if not path.exists():
            return RunState()
        return RunState.from_json(self.read_json(path))

    def save_run_state(self, state: RunState) -> None:
        state.updatedAt = now_iso()
        self.write_json_atomic(self.run_state_path(), state.to_json())

    def save_signals(self, experiment_type: str, signals: dict[str, Any]) -> None:
        self.write_json_atomic(self.signals_path(experiment_type), signals)

    def load_signals(self, experiment_type: str) -> dict[str, Any]:
        path = self.signals_path(experiment_type)
        if not path.exists():
            return {"elements": {}, "pairs": {}}
        return self.read_json(path)

    def experiment_dir(self, experiment_type: str, experiment_id: str) -> Path:
        return self.artifacts_dir / experiment_type / experiment_id

    def workspace_for(self, experiment_type: str) -> Path:
        return self.workspace_dir / experiment_type

    def reset_workspace_from(self, experiment_type: str, source: Path) -> Path:
        workspace = self.workspace_for(experiment_type)
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(source, workspace)
        return workspace

    def archive_artifact(
        self, experiment_type: str, experiment_id: str, archive_globs: list[str]
    ) -> Path:
        exp_dir = self.experiment_dir(experiment_type, experiment_id)
        artifact_dir = exp_dir / "artifact"
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for pattern in archive_globs:
            for src in self.root.glob(pattern):
                if src.is_file():
                    rel = src.relative_to(self.root)
                    dest = artifact_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                elif src.is_dir():
                    dest = artifact_dir / src.relative_to(self.root)
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest)
        return artifact_dir

    def write_experiment_json(
        self, experiment_type: str, experiment_id: str, name: str, data: dict[str, Any]
    ) -> None:
        path = self.experiment_dir(experiment_type, experiment_id) / name
        self.write_json_atomic(path, data)

    def append_log(self, name: str, event: dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        event = {"time": now_iso(), **event}
        with (self.logs_dir / name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

