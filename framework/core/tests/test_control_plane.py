"""
Unit tests for control_plane.py — framework-layer logic only.
These tests must run without an Anthropic API key (no real LLM calls).
"""
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Make sure framework/core is importable
# ---------------------------------------------------------------------------
FRAMEWORK_CORE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FRAMEWORK_CORE_DIR))

# Patch the Anthropic client at import time so no key is needed
with patch("anthropic.Anthropic"):
    import control_plane as cp


@pytest.fixture(autouse=True)
def minimal_seam_constants():
    """Set required seam constants to neutral test values.

    After the sentinel-default refactor, METRIC_NAME, METRIC_DIRECTION, etc.
    are empty strings until apply_instance_config() is called.  Tests that
    exercise functions depending on these globals set them here and restore
    them after each test so tests remain independent.
    """
    saved = {
        "METRIC_NAME":      cp.METRIC_NAME,
        "METRIC_DIRECTION": cp.METRIC_DIRECTION,
        "BENCHMARK_SCRIPT": cp.BENCHMARK_SCRIPT,
    }
    cp.METRIC_NAME      = "test_metric"
    cp.METRIC_DIRECTION = "lower_is_better"
    cp.BENCHMARK_SCRIPT = "benchmark.sh"
    yield
    cp.METRIC_NAME      = saved["METRIC_NAME"]
    cp.METRIC_DIRECTION = saved["METRIC_DIRECTION"]
    cp.BENCHMARK_SCRIPT = saved["BENCHMARK_SCRIPT"]


# ===========================================================================
# is_improvement
# ===========================================================================

class TestIsImprovement:
    def test_lower_is_better_improvement(self):
        assert cp.is_improvement(100.0, 90.0, "lower_is_better") is True

    def test_lower_is_better_regression(self):
        assert cp.is_improvement(90.0, 100.0, "lower_is_better") is False

    def test_lower_is_better_equal(self):
        assert cp.is_improvement(100.0, 100.0, "lower_is_better") is False

    def test_higher_is_better_improvement(self):
        assert cp.is_improvement(0.80, 0.95, "higher_is_better") is True

    def test_higher_is_better_regression(self):
        assert cp.is_improvement(0.95, 0.80, "higher_is_better") is False

    def test_higher_is_better_equal(self):
        assert cp.is_improvement(0.80, 0.80, "higher_is_better") is False


# ===========================================================================
# format_delta
# ===========================================================================

class TestFormatDelta:
    def test_lower_improvement_shows_positive(self):
        # before=100, after=80 → 20% improvement (lower is better)
        result = cp.format_delta(100.0, 80.0, "lower_is_better")
        assert result == "+20.0%"

    def test_lower_regression_shows_negative(self):
        # before=80, after=100 → -25% (regression)
        result = cp.format_delta(80.0, 100.0, "lower_is_better")
        assert result == "-25.0%"

    def test_higher_improvement_shows_positive(self):
        result = cp.format_delta(0.80, 0.90, "higher_is_better")
        assert result.startswith("+")

    def test_zero_before_returns_na(self):
        assert cp.format_delta(0.0, 50.0) == "N/A"

    def test_default_direction_matches_module_constant(self):
        # Verify the default direction is consumed correctly
        result = cp.format_delta(100.0, 90.0)
        if cp.METRIC_DIRECTION == "lower_is_better":
            assert result == "+10.0%"
        else:
            assert result == "-10.0%"


# ===========================================================================
# render_template
# ===========================================================================

class TestRenderTemplate:
    def test_basic_substitution(self, tmp_path):
        tpl = tmp_path / "t.md"
        tpl.write_text("Hello {{NAME}}, metric is {{METRIC}}.")
        result = cp.render_template(tpl, {"NAME": "World", "METRIC": "42"})
        assert result == "Hello World, metric is 42."

    def test_missing_key_left_as_placeholder(self, tmp_path):
        tpl = tmp_path / "t.md"
        tpl.write_text("{{A}} and {{B}}")
        result = cp.render_template(tpl, {"A": "x"})
        assert "{{B}}" in result

    def test_no_double_substitution(self, tmp_path):
        """A value that looks like a placeholder should NOT be expanded again."""
        tpl = tmp_path / "t.md"
        tpl.write_text("{{OUTER}}")
        # OUTER expands to text that looks like a placeholder
        result = cp.render_template(tpl, {"OUTER": "{{INNER}}", "INNER": "BAD"})
        # render_template is a single-pass replace — {{INNER}} should remain
        assert "BAD" not in result
        assert "{{INNER}}" in result


class TestRenderTemplateForPhase:
    def test_strips_inactive_phase(self, tmp_path):
        tpl = tmp_path / "two_phase.md"
        tpl.write_text(
            "## PHASE = implement\nimpl content\n## PHASE = report\nreport content\n"
        )
        result = cp.render_template_for_phase(tpl, "implement", {})
        assert "impl content" in result
        assert "report content" not in result

    def test_keeps_active_phase(self, tmp_path):
        tpl = tmp_path / "two_phase.md"
        tpl.write_text(
            "## PHASE = implement\nimpl content\n## PHASE = report\nreport content\n"
        )
        result = cp.render_template_for_phase(tpl, "report", {})
        assert "report content" in result
        assert "impl content" not in result

    def test_substitutions_applied(self, tmp_path):
        tpl = tmp_path / "t.md"
        tpl.write_text("## PHASE = q\n{{METRIC_NAME}}\n")
        result = cp.render_template_for_phase(tpl, "q", {"METRIC_NAME": "runtime_ms"})
        assert "runtime_ms" in result


# ===========================================================================
# Benchmark output parser
# ===========================================================================

class TestRunBenchmarkParsing:
    """Test the METRIC line parsing logic embedded in run_benchmark().

    We mock subprocess.run to inject synthetic stdout.
    """

    def _run_with_stdout(self, stdout: str, returncode: int = 0):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=returncode, stdout=stdout, stderr=""
            )
            return cp.run_benchmark()

    def test_valid_metric_line(self):
        result = self._run_with_stdout(f"METRIC {cp.METRIC_NAME}=42.5\n")
        assert result is not None
        assert result[cp.METRIC_NAME] == 42.5

    def test_no_metric_line_returns_none(self):
        result = self._run_with_stdout("some other output\n")
        assert result is None

    def test_nonzero_exit_returns_none(self):
        result = self._run_with_stdout(f"METRIC {cp.METRIC_NAME}=10\n", returncode=1)
        assert result is None

    def test_multiple_metrics_all_parsed(self):
        result = self._run_with_stdout(
            f"METRIC {cp.METRIC_NAME}=22\nMETRIC memory_mb=128\n"
        )
        assert result is not None
        assert result[cp.METRIC_NAME] == 22.0
        assert result.get("memory_mb") == 128.0

    def test_duplicate_key_last_wins(self):
        result = self._run_with_stdout(
            f"METRIC {cp.METRIC_NAME}=10\nMETRIC {cp.METRIC_NAME}=20\n"
        )
        assert result is not None
        assert result[cp.METRIC_NAME] == 20.0

    def test_empty_stdout_returns_none(self):
        result = self._run_with_stdout("")
        assert result is None


# ===========================================================================
# Verifier output parsing
# ===========================================================================

class TestRunVerifierParsing:
    """Test run_verifier() generic subprocess wrapper."""

    def _write_verdict(self, vpath, verdict_dict):
        """Return a side_effect fn that writes verdict.json when subprocess is called."""
        def side_effect(*args, **kwargs):
            vpath.write_text(json.dumps(verdict_dict))
            return MagicMock(returncode=0, stdout="", stderr="")
        return side_effect

    def test_pass_verdict(self, tmp_path):
        verdict = {"status": "PASS", "detail": "", "failures": []}
        vpath = tmp_path / "verdict.json"
        with patch("subprocess.run", side_effect=self._write_verdict(vpath, verdict)), \
             patch.object(cp, "VERDICT_OUTPUT_PATH", vpath):
            result = cp.run_verifier()
        assert result["status"] == "PASS"

    def test_no_verdict_file_returns_fail(self, tmp_path):
        with patch("subprocess.run") as mock_run, \
             patch.object(cp, "VERDICT_OUTPUT_PATH", tmp_path / "nonexistent.json"):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cp.run_verifier()

        assert result["status"] == "FAIL"
        assert "no output" in result["detail"].lower()

    def test_malformed_json_returns_fail(self, tmp_path):
        vpath = tmp_path / "verdict.json"
        def se(*a, **kw):
            vpath.write_text("not json {{{")
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", side_effect=se), \
             patch.object(cp, "VERDICT_OUTPUT_PATH", vpath):
            result = cp.run_verifier()
        assert result["status"] == "FAIL"

    def test_timeout_returns_fail(self, tmp_path):
        import subprocess as _sp
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired("cmd", 600)), \
             patch.object(cp, "VERDICT_OUTPUT_PATH", tmp_path / "v.json"):
            result = cp.run_verifier()

        assert result["status"] == "FAIL"
        assert "timed out" in result["detail"]


# ===========================================================================
# verifier_passed
# ===========================================================================

class TestVerifierPassed:
    def test_pass_status(self):
        assert cp.verifier_passed({"status": "PASS"}) is True

    def test_ok_status(self):
        assert cp.verifier_passed({"status": "OK"}) is True

    def test_fail_status(self):
        assert cp.verifier_passed({"status": "FAIL"}) is False

    def test_missing_status(self):
        assert cp.verifier_passed({}) is False


# ===========================================================================
# METRIC_DIRECTION_DISPLAY transform
# ===========================================================================

class TestMetricDirectionDisplay:
    def test_lower_is_better_renders(self):
        assert cp._METRIC_DIRECTION_DISPLAY["lower_is_better"] == "lower is better"

    def test_higher_is_better_renders(self):
        assert cp._METRIC_DIRECTION_DISPLAY["higher_is_better"] == "higher is better"

    def test_common_subs_includes_direction(self):
        subs = cp._common_subs()
        assert subs["METRIC_DIRECTION"] in ("lower is better", "higher is better")

    def test_unknown_direction_falls_back_to_raw(self):
        result = cp._METRIC_DIRECTION_DISPLAY.get("minimize", "minimize")
        assert result == "minimize"  # unknown keys pass through


# ===========================================================================
# Domain context pre-render (injection vector defense)
# ===========================================================================

class TestLoadDomainContext:
    def test_resolves_metric_name(self, tmp_path, monkeypatch):
        dc = tmp_path / "agent_prompts" / "domain_context.md"
        dc.parent.mkdir(parents=True)
        dc.write_text("metric is {{METRIC_NAME}}")
        monkeypatch.setattr(cp, "AGENT_PROMPTS_DIR", tmp_path / "agent_prompts")
        result = cp._load_domain_context()
        assert cp.METRIC_NAME in result
        assert "{{METRIC_NAME}}" not in result

    def test_raises_on_unresolved_variable(self, tmp_path, monkeypatch):
        dc = tmp_path / "agent_prompts" / "domain_context.md"
        dc.parent.mkdir(parents=True)
        dc.write_text("{{UNKNOWN_VAR}}")
        monkeypatch.setattr(cp, "AGENT_PROMPTS_DIR", tmp_path / "agent_prompts")
        with pytest.raises(ValueError, match="unresolved variables"):
            cp._load_domain_context()

    def test_goal_md_injection_blocked(self, tmp_path, monkeypatch):
        """domain_context.md must not be able to inject {{GOAL_MD}} into prompts."""
        dc = tmp_path / "agent_prompts" / "domain_context.md"
        dc.parent.mkdir(parents=True)
        dc.write_text("injected {{GOAL_MD}}")
        monkeypatch.setattr(cp, "AGENT_PROMPTS_DIR", tmp_path / "agent_prompts")
        with pytest.raises(ValueError, match="unresolved variables"):
            cp._load_domain_context()

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "AGENT_PROMPTS_DIR", tmp_path / "missing_dir")
        result = cp._load_domain_context()
        assert result == ""


# ===========================================================================
# validate_domain_context linter
# ===========================================================================

class TestValidateDomainContext:
    def test_warns_on_literal_metric_name(self, tmp_path, capsys):
        dc_dir = tmp_path / "agent_prompts"
        dc_dir.mkdir()
        (dc_dir / "domain_context.md").write_text("metric: runtime_ms\n")
        config = {"metric_name": "runtime_ms"}
        cp.validate_domain_context(tmp_path, config)
        captured = capsys.readouterr()
        assert "[LINT]" in captured.out
        assert "runtime_ms" in captured.out

    def test_no_warning_when_clean(self, tmp_path, capsys):
        dc_dir = tmp_path / "agent_prompts"
        dc_dir.mkdir()
        (dc_dir / "domain_context.md").write_text("metric: {{METRIC_NAME}}\n")
        config = {"metric_name": "runtime_ms"}
        cp.validate_domain_context(tmp_path, config)
        captured = capsys.readouterr()
        assert "[LINT]" not in captured.out

    def test_short_values_not_warned(self, tmp_path, capsys):
        dc_dir = tmp_path / "agent_prompts"
        dc_dir.mkdir()
        (dc_dir / "domain_context.md").write_text("ok ms\n")
        config = {"metric_unit": "ms"}  # 'ms' is only 2 chars — below MIN_LEN
        cp.validate_domain_context(tmp_path, config)
        captured = capsys.readouterr()
        assert "[LINT]" not in captured.out

    def test_missing_file_no_error(self, tmp_path, capsys):
        cp.validate_domain_context(tmp_path, {"metric_name": "runtime_ms"})
        captured = capsys.readouterr()
        assert "[LINT]" not in captured.out


# ===========================================================================
# hard_blocked enforcement
# ===========================================================================

class TestHardBlocked:
    def test_write_to_blocked_path_raises(self, tmp_path):
        blocked = tmp_path / "dijkstra_ref.cpp"
        blocked.write_text("// ref")
        blocked_set = frozenset({blocked})
        with patch.object(cp, "HARD_BLOCKED", blocked_set):
            with pytest.raises(PermissionError, match="hard-blocked"):
                cp.check_write_permission(blocked, "implementation")

    def test_write_inside_blocked_dir_raises(self, tmp_path):
        blocked_dir = tmp_path / "data"
        blocked_dir.mkdir()
        target = blocked_dir / "somefile.txt"
        with patch.object(cp, "HARD_BLOCKED", frozenset({blocked_dir})):
            with pytest.raises(PermissionError, match="hard-blocked"):
                cp.check_write_permission(target, "implementation")

    def test_allowed_path_does_not_raise(self, tmp_path):
        allowed = tmp_path / "sssp.cpp"
        with patch.object(cp, "HARD_BLOCKED", frozenset()), \
             patch.object(cp, "WRITE_PERMISSIONS", {"implementation": frozenset({allowed})}):
            cp.check_write_permission(allowed, "implementation")  # should not raise

    def test_agent_not_in_permissions_raises(self, tmp_path):
        target = tmp_path / "secret.txt"
        with patch.object(cp, "HARD_BLOCKED", frozenset()), \
             patch.object(cp, "WRITE_PERMISSIONS", {"implementation": frozenset({tmp_path / "other.cpp"})}):
            with pytest.raises(PermissionError):
                cp.check_write_permission(target, "implementation")


# ===========================================================================
# mbmm.init() path resolution
# ===========================================================================

class TestMbmmInit:
    def test_init_sets_store_path(self, tmp_path):
        import mbmm
        mbmm.init(tmp_path)
        assert mbmm.IDEA_STORE_PATH == tmp_path / "idea-store.json"
        assert mbmm.IDEA_ARCHIVE_PATH == tmp_path / "idea-store-archive.json"

    def test_init_resolves_relative_path(self, tmp_path):
        import mbmm
        mbmm.init(tmp_path)
        assert mbmm.IDEA_STORE_PATH.is_absolute()


# ===========================================================================
# Enhancement 1: PROFILE line parsing
# ===========================================================================

class TestProfileLineParsing:
    """Test PROFILE line parsing in run_benchmark() and _format_profile_data()."""

    def _run_with_stdout(self, stdout: str, returncode: int = 0):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=returncode, stdout=stdout, stderr=""
            )
            return cp.run_benchmark()

    def test_profile_lines_stored_in_side_channel(self):
        cp._last_profile_data.clear()
        self._run_with_stdout(
            f"METRIC {cp.METRIC_NAME}=42\nPROFILE cache_misses=1000\nPROFILE memory_kb=512\n"
        )
        assert cp._last_profile_data.get("cache_misses") == 1000.0
        assert cp._last_profile_data.get("memory_kb") == 512.0

    def test_profile_lines_capped_at_20(self):
        cp._last_profile_data.clear()
        profile_lines = "\n".join(f"PROFILE k{i}={i}" for i in range(30))
        self._run_with_stdout(f"METRIC {cp.METRIC_NAME}=42\n{profile_lines}\n")
        assert len(cp._last_profile_data) == 20

    def test_malformed_profile_value_ignored(self):
        cp._last_profile_data.clear()
        self._run_with_stdout(
            f"METRIC {cp.METRIC_NAME}=42\nPROFILE bad=notanumber\nPROFILE good=7.0\n"
        )
        assert "bad" not in cp._last_profile_data
        assert cp._last_profile_data.get("good") == 7.0

    def test_profile_cleared_on_nonzero_exit(self):
        cp._last_profile_data["stale"] = 99.0
        self._run_with_stdout(f"METRIC {cp.METRIC_NAME}=42\nPROFILE x=1\n", returncode=1)
        assert cp._last_profile_data == {}

    def test_profile_cleared_on_missing_metric(self):
        cp._last_profile_data["stale"] = 99.0
        self._run_with_stdout("PROFILE x=1\n")
        assert cp._last_profile_data == {}

    def test_format_profile_data_empty(self):
        cp._last_profile_data.clear()
        assert cp._format_profile_data() == "(no profile data collected)"

    def test_format_profile_data_nonempty(self):
        cp._last_profile_data.clear()
        cp._last_profile_data["cache_misses"] = 1000.0
        result = cp._format_profile_data()
        assert "cache_misses" in result
        assert "1000.0" in result


# ===========================================================================
# Enhancement 3: _load_recent_experiment_summaries
# ===========================================================================

class TestLoadRecentExperimentSummaries:
    """Test rolling experiment summary loader."""

    def test_n_zero_returns_disabled_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        result = cp._load_recent_experiment_summaries(0, 10)
        assert result == "(no recent experiment summaries)"

    def test_empty_artifacts_dir_returns_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        result = cp._load_recent_experiment_summaries(5, 5)
        assert result == "(no recent experiment summaries)"

    def test_loads_analysis_section_from_result_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        monkeypatch.setattr(cp, "RECENT_EXPERIMENTS_MAX_LINES_PER", 50)
        exp_dir = tmp_path / "exp-001"
        exp_dir.mkdir()
        (exp_dir / "experiment-result.md").write_text(
            "Experiment ID: exp-001\nStatus: PASS\n\n## Analysis\nPrimary cause: it worked.\n"
        )
        result = cp._load_recent_experiment_summaries(5, 1)
        assert "exp-001" in result
        assert "Primary cause" in result

    def test_skips_missing_experiment_dirs(self, tmp_path, monkeypatch):
        """Experiments 1 and 3 exist; experiment 2 is abandoned (no dir)."""
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        monkeypatch.setattr(cp, "RECENT_EXPERIMENTS_MAX_LINES_PER", 50)
        for n in (1, 3):
            d = tmp_path / f"exp-{n:03d}"
            d.mkdir()
            (d / "experiment-result.md").write_text(
                f"Experiment ID: exp-{n:03d}\nStatus: PASS\n\n## Analysis\ncause {n}\n"
            )
        result = cp._load_recent_experiment_summaries(5, 3)
        assert "exp-003" in result
        assert "exp-001" in result
        # Missing exp-002 should not cause any error

    def test_falls_back_to_full_file_when_no_analysis_section(self, tmp_path, monkeypatch):
        """Pre-Enhancement-2 files without ## Analysis section use full content."""
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        monkeypatch.setattr(cp, "RECENT_EXPERIMENTS_MAX_LINES_PER", 50)
        exp_dir = tmp_path / "exp-001"
        exp_dir.mkdir()
        (exp_dir / "experiment-result.md").write_text(
            "Experiment ID: exp-001\nStatus: FAIL-METRIC\nOld format with no Analysis heading.\n"
        )
        result = cp._load_recent_experiment_summaries(5, 1)
        assert "Old format" in result

    def test_line_cap_applied_per_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "ARTIFACTS_DIR", tmp_path)
        monkeypatch.setattr(cp, "RECENT_EXPERIMENTS_MAX_LINES_PER", 3)
        exp_dir = tmp_path / "exp-001"
        exp_dir.mkdir()
        long_content = "\n".join(f"line {i}" for i in range(20))
        (exp_dir / "experiment-result.md").write_text(
            f"Status: PASS\n\n## Analysis\n{long_content}\n"
        )
        result = cp._load_recent_experiment_summaries(5, 1)
        assert "...(truncated)" in result

    def test_instance_reload_does_not_bleed_schedule_values(self, tmp_path):
        """Fix for finding 1: missing schedule key resets to literal default, not prior global."""
        # First instance sets a non-default value
        config1 = _minimal_instance_config(tmp_path)
        config1["schedule"]["recent_experiments_window"] = 0
        cp.apply_instance_config(config1, tmp_path)
        assert cp.RECENT_EXPERIMENTS_WINDOW == 0

        # Second instance omits the key — must reset to default (5), not inherit 0
        config2 = _minimal_instance_config(tmp_path)
        config2["schedule"].pop("recent_experiments_window", None)
        cp.apply_instance_config(config2, tmp_path)
        assert cp.RECENT_EXPERIMENTS_WINDOW == 5


def _minimal_instance_config(tmp_path: Path) -> dict:
    """Build a minimal valid instance config for testing apply_instance_config()."""
    (tmp_path / "agent_prompts").mkdir(exist_ok=True)
    (tmp_path / "agent_prompts" / "domain_context.md").write_text("")
    (tmp_path / "verifier" / "verdict.json").parent.mkdir(parents=True, exist_ok=True)
    return {
        "artifact": "src/artifact.cpp",
        "artifact_snapshot_name": "artifact.cpp",
        "artifact_xml_tag": "artifact_cpp",
        "artifact_language": "C++17",
        "params": "params.json",
        "build_command": "g++ -O2",
        "benchmark": "benchmark.sh",
        "verifier_command": ["bash", "verifier/verify.sh"],
        "verdict_output_path": "verifier/verdict.json",
        "metric_name": "runtime_ms",
        "metric_direction": "lower_is_better",
        "benchmark_description": "",
        "correctness_constraint": "",
        "fallback_queries": [],
        "hard_blocked": [],
        "schedule": {
            "explore_every_N": 5,
            "explore_budget": 2,
            "max_debate_rounds": 2,
            "scout_every_N": 10,
        },
    }
