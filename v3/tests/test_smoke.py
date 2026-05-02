from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from autoresearch.controller import Controller
from autoresearch.descriptors.example import DESCRIPTOR
from autoresearch.descriptors.neuraloperator_fno import DESCRIPTOR as FNO_DESCRIPTOR
from autoresearch.llm import MockLlmClient


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

    def _copy_fixture_tree(self, source: Path, target: Path) -> None:
        import shutil

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)


if __name__ == "__main__":
    unittest.main()
