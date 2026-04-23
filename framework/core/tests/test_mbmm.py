"""
Unit tests for mbmm.py — Enhancement 4 failure-memory logic.

Covers:
- _should_overwrite_failure: confidence-ranked overwrite rule
- apply_signals: penalize writes failure memory, revive clears it
- record_citation_outcome: kept result clears stale failure memory
- format_window_for_prompt: renders medium/high, suppresses low
"""
import json
import sys
from pathlib import Path

import pytest

FRAMEWORK_CORE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(FRAMEWORK_CORE_DIR))

import mbmm


def _make_store(tmp_path: Path, ideas: list[dict]) -> None:
    mbmm.init(tmp_path)
    store = tmp_path / "idea-store.json"
    store.write_text(json.dumps(ideas))


def _load_store(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "idea-store.json").read_text())


def _base_idea(id_: str = "idea-001") -> dict:
    return {
        "id":                 id_,
        "title":              "test idea",
        "mechanism":          "some mechanism",
        "signal":             "5% improvement",
        "status":             "active",
        "source":             "recorder",
        "initial_score":      0.5,
        "base_score":         0.5,
        "added_at_exp":       0,
        "times_in_window":    0,
        "last_in_window_exp": 0,
        "times_cited":        0,
        "times_kept":         0,
        "blocked_by":         None,
        "superseded_by":      None,
        # failure-memory fields absent in older entries — .get() must handle this
    }


# ===========================================================================
# _should_overwrite_failure
# ===========================================================================

class TestShouldOverwriteFailure:
    def test_none_into_empty_returns_false(self):
        assert mbmm._should_overwrite_failure(None, None) is False

    def test_medium_into_empty_returns_true(self):
        assert mbmm._should_overwrite_failure(None, "medium") is True

    def test_low_into_empty_returns_true(self):
        assert mbmm._should_overwrite_failure(None, "low") is True

    def test_high_overwrites_medium(self):
        assert mbmm._should_overwrite_failure("medium", "high") is True

    def test_medium_overwrites_low(self):
        assert mbmm._should_overwrite_failure("low", "medium") is True

    def test_medium_does_not_overwrite_high(self):
        assert mbmm._should_overwrite_failure("high", "medium") is False

    def test_same_level_does_not_overwrite(self):
        assert mbmm._should_overwrite_failure("medium", "medium") is False

    def test_none_does_not_overwrite_existing(self):
        assert mbmm._should_overwrite_failure("low", None) is False


# ===========================================================================
# apply_signals: failure memory on penalize
# ===========================================================================

class TestApplySignalsFailureMemory:
    def test_penalize_with_medium_hypothesis_writes_fields(self, tmp_path):
        _make_store(tmp_path, [_base_idea()])
        signals = [{
            "id": "idea-001",
            "signal": "penalize",
            "reason": "regression",
            "failure_hypothesis": "Sorting in hot loop caused overhead.",
            "failure_confidence": "medium",
            "failure_hypothesis_from": "exp-042",
            "revive_condition": "Hoist sort out of loop.",
        }]
        mbmm.apply_signals(signals, [], current_exp=42)
        idea = _load_store(tmp_path)[0]
        assert idea["failure_hypothesis"] == "Sorting in hot loop caused overhead."
        assert idea["failure_confidence"] == "medium"
        assert idea["failure_hypothesis_from"] == "exp-042"
        assert idea["revive_condition"] == "Hoist sort out of loop."

    def test_penalize_without_hypothesis_leaves_memory_unchanged(self, tmp_path):
        idea = _base_idea()
        _make_store(tmp_path, [idea])
        signals = [{"id": "idea-001", "signal": "penalize", "reason": "regression"}]
        mbmm.apply_signals(signals, [], current_exp=1)
        stored = _load_store(tmp_path)[0]
        assert stored.get("failure_hypothesis") is None
        assert stored.get("failure_confidence") is None

    def test_medium_does_not_overwrite_existing_high(self, tmp_path):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Prior strong hypothesis."
        idea["failure_confidence"]      = "high"
        idea["failure_hypothesis_from"] = "exp-010"
        idea["revive_condition"]        = "Prior revive condition."
        _make_store(tmp_path, [idea])
        signals = [{
            "id": "idea-001",
            "signal": "penalize",
            "reason": "new regression",
            "failure_hypothesis": "Weaker new hypothesis.",
            "failure_confidence": "medium",
            "failure_hypothesis_from": "exp-020",
        }]
        mbmm.apply_signals(signals, [], current_exp=20)
        stored = _load_store(tmp_path)[0]
        # Original high-confidence hypothesis must survive
        assert stored["failure_hypothesis"] == "Prior strong hypothesis."
        assert stored["failure_confidence"] == "high"
        assert stored["failure_hypothesis_from"] == "exp-010"

    def test_high_overwrites_existing_medium(self, tmp_path):
        idea = _base_idea()
        idea["failure_hypothesis"]  = "Medium hypothesis."
        idea["failure_confidence"]  = "medium"
        _make_store(tmp_path, [idea])
        signals = [{
            "id": "idea-001",
            "signal": "penalize",
            "reason": "regression",
            "failure_hypothesis": "Strong new hypothesis.",
            "failure_confidence": "high",
            "failure_hypothesis_from": "exp-030",
        }]
        mbmm.apply_signals(signals, [], current_exp=30)
        stored = _load_store(tmp_path)[0]
        assert stored["failure_hypothesis"] == "Strong new hypothesis."
        assert stored["failure_confidence"] == "high"

    def test_fail_verifier_signal_does_not_write_memory(self, tmp_path):
        """FAIL-VERIFIER path: apply_signals() is called with a penalize signal
        only if the attribution agent incorrectly emits one despite the rule.
        The confidence-gate prevents writing when no failure_hypothesis is present
        — this tests that a signal with no hypothesis fields leaves memory untouched."""
        _make_store(tmp_path, [_base_idea()])
        # Simulates what a correctly-behaving attribution agent would emit on FAIL-VERIFIER:
        # no penalize signal at all, or a penalize with no hypothesis fields.
        signals = [{"id": "idea-001", "signal": "penalize", "reason": "verifier bug (no hypothesis)"}]
        mbmm.apply_signals(signals, [], current_exp=5)
        stored = _load_store(tmp_path)[0]
        assert stored.get("failure_hypothesis") is None
        assert stored.get("failure_confidence") is None


# ===========================================================================
# apply_signals: revive clears failure memory
# ===========================================================================

class TestApplySignalsReviveClears:
    def test_revive_clears_all_failure_memory_fields(self, tmp_path):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Old hypothesis."
        idea["failure_confidence"]      = "high"
        idea["failure_hypothesis_from"] = "exp-010"
        idea["revive_condition"]        = "Old revive condition."
        _make_store(tmp_path, [idea])
        signals = [{"id": "idea-001", "signal": "revive", "reason": "conditions met"}]
        mbmm.apply_signals(signals, [], current_exp=20)
        stored = _load_store(tmp_path)[0]
        assert stored.get("failure_hypothesis")      is None
        assert stored.get("failure_confidence")      is None
        assert stored.get("failure_hypothesis_from") is None
        assert stored.get("revive_condition")        is None
        assert stored["status"] == "active"


# ===========================================================================
# record_citation_outcome: kept clears stale failure memory
# ===========================================================================

class TestRecordCitationOutcomeClears:
    def test_kept_result_clears_stale_failure_memory(self, tmp_path):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Old hypothesis."
        idea["failure_confidence"]      = "medium"
        idea["failure_hypothesis_from"] = "exp-010"
        idea["revive_condition"]        = "Old revive condition."
        _make_store(tmp_path, [idea])
        mbmm.record_citation_outcome(["idea-001"], kept=True, fail_type="", current_exp=20)
        stored = _load_store(tmp_path)[0]
        assert stored.get("failure_hypothesis")      is None
        assert stored.get("failure_confidence")      is None
        assert stored.get("failure_hypothesis_from") is None
        assert stored.get("revive_condition")        is None

    def test_fail_metric_does_not_clear_failure_memory(self, tmp_path):
        """FAIL-METRIC citation outcome penalizes score but should not touch failure memory."""
        idea = _base_idea()
        idea["failure_hypothesis"] = "Existing hypothesis."
        idea["failure_confidence"] = "high"
        _make_store(tmp_path, [idea])
        mbmm.record_citation_outcome(["idea-001"], kept=False, fail_type="FAIL-METRIC", current_exp=5)
        stored = _load_store(tmp_path)[0]
        # failure memory untouched (only apply_signals() writes it)
        assert stored.get("failure_hypothesis") == "Existing hypothesis."

    def test_fail_verifier_does_not_touch_idea_at_all(self, tmp_path):
        """FAIL-VERIFIER → no signal, score unchanged, memory unchanged."""
        idea = _base_idea()
        original_score = idea["base_score"]
        _make_store(tmp_path, [idea])
        mbmm.record_citation_outcome(["idea-001"], kept=False, fail_type="FAIL-VERIFIER", current_exp=3)
        stored = _load_store(tmp_path)[0]
        assert stored["base_score"] == original_score
        assert stored.get("failure_hypothesis") is None


# ===========================================================================
# format_window_for_prompt: failure memory rendering
# ===========================================================================

class TestFormatWindowFailureMemoryRendering:
    def _window_text(self, idea: dict) -> str:
        return mbmm.format_window_for_prompt([idea], current_exp=10, total_experiments=10)

    def test_medium_confidence_rendered(self):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Sorting caused overhead."
        idea["failure_confidence"]      = "medium"
        idea["failure_hypothesis_from"] = "exp-042"
        idea["revive_condition"]        = "Hoist sort."
        text = self._window_text(idea)
        assert "failure_memory" in text
        assert "[medium, from exp-042]" in text
        assert "Sorting caused overhead." in text
        assert "Hoist sort." in text

    def test_high_confidence_rendered(self):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Strong evidence."
        idea["failure_confidence"]      = "high"
        idea["failure_hypothesis_from"] = "exp-010"
        text = self._window_text(idea)
        assert "[high, from exp-010]" in text

    def test_low_confidence_suppressed(self):
        """Low-confidence hypotheses must not appear in the idea window."""
        idea = _base_idea()
        idea["failure_hypothesis"]  = "Weak guess."
        idea["failure_confidence"]  = "low"
        text = self._window_text(idea)
        assert "failure_memory" not in text
        assert "Weak guess." not in text

    def test_no_failure_memory_no_extra_lines(self):
        idea = _base_idea()
        text = self._window_text(idea)
        assert "failure_memory" not in text
        assert "revive_condition" not in text

    def test_revive_condition_absent_when_not_set(self):
        idea = _base_idea()
        idea["failure_hypothesis"]      = "Some hypothesis."
        idea["failure_confidence"]      = "high"
        idea["failure_hypothesis_from"] = "exp-001"
        # revive_condition deliberately absent
        text = self._window_text(idea)
        assert "revive_condition" not in text

    def test_backward_compat_idea_without_failure_fields(self):
        """Ideas from before Enhancement 4 have no failure-memory keys — must not crash."""
        idea = _base_idea()
        # No failure-memory fields at all (simulates pre-Enhancement-4 store entry)
        text = self._window_text(idea)
        assert "failure_memory" not in text
