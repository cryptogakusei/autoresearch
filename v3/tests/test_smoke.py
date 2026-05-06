from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from autoresearch.benchmark import BenchmarkError, run_benchmark
from autoresearch.cli import main
from autoresearch.controller import Controller
from autoresearch.config_guard import validate_protected_config_paths
from autoresearch.descriptors.example import DESCRIPTOR
from autoresearch.descriptors.neuraloperator_fno import DESCRIPTOR as FNO_DESCRIPTOR
from autoresearch.llm import MockLlmClient
from autoresearch.preflight import PreflightError, run_neuraloperator_fno_gpu_preflight
from autoresearch.remote import RemoteConfig, run_remote_benchmark


class SmokeTest(unittest.TestCase):
    def test_seed_and_run_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline = tmp_path / "workspace" / "baselines" / "example"
            baseline.mkdir(parents=True)
            (baseline / "solution.txt").write_text("baseline solution\n", encoding="utf-8")

            bench = tmp_path / "benchmarks" / "example"
            bench.mkdir(parents=True)
            source_bench = Path(__file__).parents[1] / "benchmarks" / "example" / "run.py"
            (bench / "run.py").write_text(
                source_bench.read_text(encoding="utf-8"), encoding="utf-8"
            )

            controller = Controller(tmp_path, DESCRIPTOR, MockLlmClient())
            controller.seed_default()
            result = controller.run_one()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.status, "done")
            self.assertTrue(
                (tmp_path / ".autoresearch" / "state" / "example.ideas.json").exists()
            )
            self.assertTrue(
                (tmp_path / ".autoresearch" / "artifacts" / "example" / result.id).exists()
            )

    def test_cli_benchmark_candidate_runs_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate = tmp_path / "workspace" / "phase4_candidates" / "candidate_a"
            candidate.mkdir(parents=True)
            (candidate / "solution.txt").write_text(
                "candidate solution\n", encoding="utf-8"
            )

            bench = tmp_path / "benchmarks" / "example"
            bench.mkdir(parents=True)
            source_bench = Path(__file__).parents[1] / "benchmarks" / "example" / "run.py"
            (bench / "run.py").write_text(
                source_bench.read_text(encoding="utf-8"), encoding="utf-8"
            )

            exit_code = main(
                [
                    "--root",
                    str(tmp_path),
                    "--type",
                    "example",
                    "benchmark-candidate",
                    "candidate_a",
                    "--id",
                    "candidate_a",
                ]
            )

            self.assertEqual(exit_code, 0)

    def test_benchmark_timeout_is_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.dict("os.environ", {"AUTORESEARCH_REMOTE_HOST": ""}, clear=False):
                with patch(
                    "autoresearch.benchmark.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["benchmark"], 30),
                ):
                    with self.assertRaisesRegex(BenchmarkError, "timed out"):
                        run_benchmark(DESCRIPTOR, tmp_path, "timeout_case", tmp_path)

    def test_remote_benchmark_recovers_json_after_ssh_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = tmp_path / "artifact"
            artifact.mkdir()
            config = RemoteConfig(
                host="example.invalid",
                user="ubuntu",
                key_path="key.pem",
                port=22,
                remote_root="/remote/root",
                data_dir="/remote/data",
            )
            recovered = (
                '{"type":"example","artifactId":"case","metrics":{"main":{"score":1}}}'
            )

            def fake_ssh(_config, command, timeout):
                if command.startswith("mkdir -p"):
                    return subprocess.CompletedProcess([], 0, "", "")
                if command.startswith("cat "):
                    return subprocess.CompletedProcess([], 0, recovered, "")
                return subprocess.CompletedProcess(
                    [], 255, "", "Connection reset by peer"
                )

            with patch("autoresearch.remote.rsync_to"), patch(
                "autoresearch.remote.ssh_run", side_effect=fake_ssh
            ):
                result = run_remote_benchmark(
                    config=config,
                    command=["python3", "bench.py", str(artifact), "case"],
                    artifact_dir=artifact,
                    local_root=tmp_path,
                    experiment_id="case",
                    timeout=60,
                )

            self.assertEqual(result, recovered)

    def test_benchmark_failure_preserves_raw_validation(self) -> None:
        class InvalidArtifactLlm(MockLlmClient):
            def implement(self, descriptor, idea, workspace):
                result = super().implement(descriptor, idea, workspace)
                target = workspace / "solution.txt"
                target.write_text("INVALID\n", encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline = tmp_path / "workspace" / "baselines" / "example"
            baseline.mkdir(parents=True)
            (baseline / "solution.txt").write_text("baseline solution\n", encoding="utf-8")

            bench = tmp_path / "benchmarks" / "example"
            bench.mkdir(parents=True)
            source_bench = Path(__file__).parents[1] / "benchmarks" / "example" / "run.py"
            (bench / "run.py").write_text(
                source_bench.read_text(encoding="utf-8"), encoding="utf-8"
            )

            controller = Controller(tmp_path, DESCRIPTOR, InvalidArtifactLlm())
            controller.seed_default()
            result = controller.run_one()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.status, "failed")
            benchmark = (
                tmp_path
                / ".autoresearch"
                / "artifacts"
                / "example"
                / result.id
                / "benchmark.json"
            )
            import json

            data = json.loads(benchmark.read_text(encoding="utf-8"))
            self.assertIn("benchmarkValidation", data)
            self.assertIn("controllerValidation", data)
            raw_failures = data["benchmarkValidation"]["failedConstraints"]
            self.assertEqual(raw_failures[0]["reason"], "artifact contains INVALID")

    def test_neuraloperator_fno_phase0_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._copy_fixture_tree(
                Path(__file__).parents[1] / "workspace" / "baselines" / "neuraloperator_fno",
                tmp_path / "workspace" / "baselines" / "neuraloperator_fno",
            )
            self._copy_fixture_tree(
                Path(__file__).parents[1] / "benchmarks" / "neuraloperator_fno",
                tmp_path / "benchmarks" / "neuraloperator_fno",
            )

            controller = Controller(tmp_path, FNO_DESCRIPTOR, MockLlmClient())
            controller.seed_from_text(
                "phase0-reference",
                "Try a high-frequency residual branch for FNO smoke validation.",
            )
            result = controller.run_one()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.status, "done")
            self.assertIn("darcy_16_smoke", result.metrics or {})
            self.assertTrue(result.validation and result.validation.passed)
            self.assertLessEqual(
                (result.metrics or {})["darcy_16_smoke"]["peak_memory_gb"], 8.0
            )
            self.assertTrue(
                (
                    tmp_path
                    / ".autoresearch"
                    / "artifacts"
                    / "neuraloperator_fno"
                    / result.id
                    / "benchmark.json"
                ).exists()
            )

    def test_protected_config_detects_dataset_contract_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline = tmp_path / "baseline"
            workspace = tmp_path / "workspace"
            (baseline / "benchmarks").mkdir(parents=True)
            (workspace / "benchmarks").mkdir(parents=True)
            baseline_config = """{
              "shared": {"resolution": 128, "train_samples": 1000, "val_samples": 100},
              "datasets": {
                "darcy_128": {"dataset": "darcy_128", "in_channels": 3, "out_channels": 1},
                "navier_stokes_128": {"dataset": "navier_stokes_128", "in_channels": 3, "out_channels": 1}
              }
            }"""
            edited_config = """{
              "shared": {"resolution": 128, "train_samples": 1000, "val_samples": 100},
              "datasets": {
                "darcy_128": {"dataset": "darcy_128", "in_channels": 3, "out_channels": 1},
                "navier_stokes_128": {"dataset": "navier_stokes_128", "in_channels": 10, "out_channels": 1}
              }
            }"""
            (baseline / "benchmarks" / "smoke_config.json").write_text(
                baseline_config, encoding="utf-8"
            )
            (workspace / "benchmarks" / "smoke_config.json").write_text(
                edited_config, encoding="utf-8"
            )

            violations = validate_protected_config_paths(
                baseline,
                workspace,
                {
                    "benchmarks/smoke_config.json": [
                        "shared.resolution",
                        "datasets.*.in_channels",
                        "datasets.*.out_channels",
                    ]
                },
            )

            self.assertEqual(len(violations), 1)
            self.assertEqual(
                violations[0]["path"], "datasets.navier_stokes_128.in_channels"
            )
            self.assertEqual(violations[0]["expected"], 3)
            self.assertEqual(violations[0]["actual"], 10)

    def test_preflight_rejects_output_shape_mismatch(self) -> None:
        try:
            import torch  # noqa: F401
        except Exception:
            self.skipTest("torch is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace" / "current" / "neuraloperator_fno_gpu"
            model_dir = workspace / "neuraloperator_variant"
            config_dir = workspace / "benchmarks"
            model_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            (model_dir / "high_frequency_residual_fno.py").write_text(
                '''
from __future__ import annotations
import torch
from torch import nn

class BadShapeModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(()))
    def forward(self, x):
        return x[:, :1] * self.weight

def build_model(config):
    return BadShapeModel()

def frequency_weighted_mse(pred, target, high_frequency_weight=0.0):
    return torch.mean((pred - target) ** 2)
'''.lstrip(),
                encoding="utf-8",
            )
            (config_dir / "smoke_config.json").write_text(
                """{
                  "shared": {"frequency_loss_weight": 0.1},
                  "datasets": {
                    "bad": {"dataset": "bad", "in_channels": 3, "out_channels": 2}
                  }
                }""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PreflightError, "expected output shape"):
                run_neuraloperator_fno_gpu_preflight(tmp_path, workspace)

    def _copy_fixture_tree(self, source: Path, target: Path) -> None:
        import shutil

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)


if __name__ == "__main__":
    unittest.main()
