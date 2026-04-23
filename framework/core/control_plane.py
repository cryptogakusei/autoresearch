#!/usr/bin/env python3
"""
AutoResearch Control Plane (framework layer)
Runs an autonomous experiment loop: debate → implement → benchmark → verify → keep/discard

This file is generic framework code. Instance-specific configuration comes from
instance.json via apply_instance_config(). Point at an instance with --instance-dir.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import mbmm

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# _FRAMEWORK_DIR: where this file lives (framework/core/ after physical move).
# INSTANCE_DIR:   where the problem instance lives (set by apply_instance_config).
_FRAMEWORK_DIR    = Path(__file__).parent.resolve()
AGENT_PROMPTS_DIR = _FRAMEWORK_DIR / "agent_prompts"  # generic prompt templates

# All instance-dir paths are initialised here and re-set by _set_instance_paths().
# They start pointing at _FRAMEWORK_DIR so the module is importable before
# apply_instance_config() is called (e.g., in unit tests).
INSTANCE_DIR: Path = _FRAMEWORK_DIR
ARTIFACT_PATH: Path | None = None  # set by apply_instance_config() from instance.json "artifact"

DATA_DIR              = _FRAMEWORK_DIR / "data"
GOAL_MD               = _FRAMEWORK_DIR / "goal.md"
REFERENCES_MD         = _FRAMEWORK_DIR / "context" / "references.md"
MASTER_JSON           = _FRAMEWORK_DIR / "master.json"
RESULTS_TSV           = _FRAMEWORK_DIR / "results.tsv"
PROGRESS_LOG          = _FRAMEWORK_DIR / "progress.log"
EXPLOITATION_PROGRESS = _FRAMEWORK_DIR / "exploitation-progress.json"
PROPOSAL_MD           = _FRAMEWORK_DIR / "proposal.md"
OBJECTIONS_MD         = _FRAMEWORK_DIR / "objections.md"
FINAL_PROPOSAL_MD     = _FRAMEWORK_DIR / "final-proposal.md"
EXPERIMENT_RESULT_MD  = _FRAMEWORK_DIR / "experiment-result.md"
EXPLORATION_RESULT_MD = _FRAMEWORK_DIR / "exploration-result.md"
UNEXPLORED_IDEAS_MD   = _FRAMEWORK_DIR / "unexplored-ideas.md"
DO_NOT_REPEAT_MD      = _FRAMEWORK_DIR / "do-not-repeat.md"
PARAMS_JSON           = _FRAMEWORK_DIR / "params.json"
ARTIFACTS_DIR         = _FRAMEWORK_DIR / "artifacts"
IDEA_STORE_JSON       = _FRAMEWORK_DIR / "idea-store.json"


def _set_instance_paths(d: Path) -> None:
    """Redirect all instance-dir-based path globals to directory *d*.

    Called once at startup from apply_instance_config() with the real instance
    directory.  The initial module-level assignments above use _FRAMEWORK_DIR as
    a safe placeholder so the module can be imported without a live instance.
    """
    global INSTANCE_DIR, ARTIFACT_PATH
    global DATA_DIR, GOAL_MD, REFERENCES_MD, MASTER_JSON, RESULTS_TSV
    global PROGRESS_LOG, EXPLOITATION_PROGRESS
    global PROPOSAL_MD, OBJECTIONS_MD, FINAL_PROPOSAL_MD
    global EXPERIMENT_RESULT_MD, EXPLORATION_RESULT_MD
    global UNEXPLORED_IDEAS_MD, DO_NOT_REPEAT_MD
    global PARAMS_JSON, ARTIFACTS_DIR, IDEA_STORE_JSON
    INSTANCE_DIR          = d
    DATA_DIR              = d / "data"
    GOAL_MD               = d / "goal.md"
    REFERENCES_MD         = d / "context" / "references.md"
    MASTER_JSON           = d / "master.json"
    RESULTS_TSV           = d / "results.tsv"
    PROGRESS_LOG          = d / "progress.log"
    EXPLOITATION_PROGRESS = d / "exploitation-progress.json"
    PROPOSAL_MD           = d / "proposal.md"
    OBJECTIONS_MD         = d / "objections.md"
    FINAL_PROPOSAL_MD     = d / "final-proposal.md"
    EXPERIMENT_RESULT_MD  = d / "experiment-result.md"
    EXPLORATION_RESULT_MD = d / "exploration-result.md"
    UNEXPLORED_IDEAS_MD   = d / "unexplored-ideas.md"
    DO_NOT_REPEAT_MD      = d / "do-not-repeat.md"
    PARAMS_JSON           = d / "params.json"
    ARTIFACTS_DIR         = d / "artifacts"
    IDEA_STORE_JSON       = d / "idea-store.json"

MODEL = "claude-opus-4-6"
MAX_TOKENS_IMPLEMENTATION = 32000   # override via instance.json "max_tokens_implementation"
MAX_TOKENS_DEFAULT        = 8192    # override via instance.json "max_tokens_default"

# ---------------------------------------------------------------------------
# Seam constants — all MUST be set by apply_instance_config() before use.
# None is the canonical unset sentinel: it is not a valid path or string,
# so accidental use raises TypeError/AttributeError immediately rather than
# silently operating on a wrong-but-plausible value (e.g. Path() == ".").
# ---------------------------------------------------------------------------
ARTIFACT_XML_TAG:      str | None       = None  # instance.json: artifact_xml_tag
ARTIFACT_SNAPSHOT_NAME: str | None      = None  # instance.json: artifact_snapshot_name
METRIC_NAME:           str | None       = None  # instance.json: metric_name
BENCHMARK_SCRIPT:      str | None       = None  # instance.json: benchmark
FALLBACK_QUERIES:      list[str]        = []    # instance.json: fallback_queries (empty = no fallback)
METRIC_DIRECTION:      str | None       = None  # instance.json: metric_direction

# Schedule defaults — safe numerics so the loop can boot even if schedule
# section is omitted from instance.json.
SCHEDULE_EXPLORE_EVERY_N  = 5
SCHEDULE_EXPLORE_BUDGET   = 2
SCHEDULE_MAX_DEBATE_ROUNDS = 2
SCHEDULE_SCOUT_EVERY_N    = 10

# Enhancement 1: PROFILE line side-channel.
# Populated by run_benchmark() after each run; cleared on failure or instance reload.
# Module-level because the benchmark result needs to be available to agents called
# later in the same experiment (report phase, Researcher A in the next debate).
_last_profile_data: dict[str, float] = {}

# Enhancement 3: rolling experiment summary window.
# Configurable via instance.json schedule block.
RECENT_EXPERIMENTS_WINDOW: int       = 5   # number of recent summaries injected into Researcher A; 0 = disabled
RECENT_EXPERIMENTS_MAX_LINES_PER: int = 50  # per-experiment-result line cap to control token budget


def _assert_instance_configured() -> None:
    """Raise RuntimeError if required seam constants are still None.

    Called at the start of cmd_setup() and cmd_run() so any missing
    configuration is caught before the loop does any real work.
    """
    missing = []
    if ARTIFACT_PATH        is None: missing.append("ARTIFACT_PATH        (instance.json: artifact)")
    if ARTIFACT_XML_TAG     is None: missing.append("ARTIFACT_XML_TAG     (instance.json: artifact_xml_tag)")
    if ARTIFACT_SNAPSHOT_NAME is None: missing.append("ARTIFACT_SNAPSHOT_NAME (instance.json: artifact_snapshot_name)")
    if METRIC_NAME          is None: missing.append("METRIC_NAME          (instance.json: metric_name)")
    if METRIC_DIRECTION     is None: missing.append("METRIC_DIRECTION     (instance.json: metric_direction)")
    if BENCHMARK_SCRIPT     is None: missing.append("BENCHMARK_SCRIPT     (instance.json: benchmark)")
    if VERIFIER_COMMAND     is None: missing.append("VERIFIER_COMMAND     (instance.json: verifier_command)")
    if VERDICT_OUTPUT_PATH  is None: missing.append("VERDICT_OUTPUT_PATH  (instance.json: verdict_output_path)")
    if missing:
        raise RuntimeError(
            "Instance not configured — call apply_instance_config() before running.\n"
            "Unset constants:\n" + "\n".join(f"  {m}" for m in missing)
        )


def is_improvement(before: float, after: float, direction: str | None = None) -> bool:
    """Return True if after is a better metric value than before.

    Uses METRIC_DIRECTION global when direction is not supplied explicitly,
    so the loop body never needs to pass it.
    """
    d = direction if direction is not None else METRIC_DIRECTION
    if d is None:
        raise RuntimeError("METRIC_DIRECTION is not set — call apply_instance_config() first.")
    if d == "lower_is_better":
        return after < before
    return after > before  # higher_is_better


def format_delta(before: float, after: float, direction: str | None = None) -> str:
    """Return a signed percentage string where positive = improvement."""
    d = direction if direction is not None else METRIC_DIRECTION
    if d is None:
        raise RuntimeError("METRIC_DIRECTION is not set — call apply_instance_config() first.")
    if before == 0:
        return "N/A"
    if d == "lower_is_better":
        pct = (before - after) / before * 100
    else:
        pct = (after - before) / before * 100
    return f"{pct:+.1f}%"


# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------
# Paths no agent may ever write — rebuilt by apply_instance_config() from
# instance.json's "hard_blocked" field (relative paths resolved to INSTANCE_DIR).
HARD_BLOCKED: frozenset[Path] = frozenset({DATA_DIR})

# Per-agent write allowlists.  None == unrestricted (control-plane itself).
# Rebuilt by apply_instance_config() once ARTIFACT_PATH is known.
WRITE_PERMISSIONS: dict[str, frozenset[Path] | None] = {
    "researcher-a":     frozenset({PROPOSAL_MD}),
    "researcher-b":     frozenset({OBJECTIONS_MD}),
    "implementation":   frozenset({ARTIFACT_PATH, PARAMS_JSON, EXPERIMENT_RESULT_MD}),
    "exploration":      frozenset({ARTIFACT_PATH, PARAMS_JSON, EXPLORATION_RESULT_MD}),
    "attribution":         frozenset({UNEXPLORED_IDEAS_MD, DO_NOT_REPEAT_MD}),
    "literature-scout": frozenset({REFERENCES_MD}),
    "control-plane":    None,  # setup, restore, promote — unrestricted
    # idea-store.json is intentionally absent from all agent allowlists.
    # Agents return update payloads; control-plane applies them via mbmm.
}


def check_write_permission(path: Path, agent: str) -> None:
    """Raise PermissionError if agent is not allowed to write path."""
    abs_path = path.resolve()
    for blocked in HARD_BLOCKED:
        b = blocked.resolve()
        if abs_path == b or str(abs_path).startswith(str(b) + os.sep):
            raise PermissionError(
                f"[PERMISSION DENIED] agent='{agent}' → hard-blocked path: {path}"
            )
    allowed = WRITE_PERMISSIONS.get(agent)
    if allowed is not None:
        resolved = {p.resolve() for p in allowed}
        if abs_path not in resolved:
            raise PermissionError(
                f"[PERMISSION DENIED] agent='{agent}' → {path} not in allowlist "
                f"({[str(p) for p in allowed]})"
            )


def guarded_write(path: Path, content: str, agent: str) -> None:
    """write_file with permission enforcement."""
    check_write_permission(path, agent)
    write_file(path, content)


def guarded_append(path: Path, content: str, agent: str) -> None:
    """Append to file with permission enforcement."""
    check_write_permission(path, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Domain context linter (5k)
# ---------------------------------------------------------------------------
_LINTER_MIN_LEN = 6  # don't warn on trivially short values like "" or "C"


def validate_domain_context(instance_dir: Path, config: dict[str, Any]) -> None:
    """Warn if any instance.json string value appears literally in domain_context.md.

    This is a linter, not a hard error — it catches cases where an owner hardcoded a
    value (e.g. 'runtime_ms') that should be written as {{METRIC_NAME}} instead.
    """
    domain_ctx_path = instance_dir / "agent_prompts" / "domain_context.md"
    if not domain_ctx_path.exists():
        return
    text = domain_ctx_path.read_text()
    for key, val in config.items():
        if not isinstance(val, str):
            continue
        if len(val) < _LINTER_MIN_LEN:
            continue
        if val in text:
            print(
                f"  [LINT] domain_context.md contains literal '{val}' "
                f"(from instance.json field '{key}'). "
                f"Use the corresponding {{{{VARIABLE}}}} placeholder instead."
            )


# ---------------------------------------------------------------------------
# Anthropic client (lazy-initialized — allows unit tests without an API key)
# ---------------------------------------------------------------------------
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return default


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def render_template(template_path: Path, substitutions: dict[str, str]) -> str:
    """Single-pass substitution — values are NOT re-scanned for further patterns."""
    template = template_path.read_text()
    return _apply_substitutions(template, substitutions)


def _apply_substitutions(text: str, substitutions: dict[str, str]) -> str:
    """Replace all {{KEY}} patterns in a single regex pass (no double-substitution)."""
    if not substitutions:
        return text
    pattern = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")
    def replacer(m: re.Match) -> str:
        return substitutions.get(m.group(1), m.group(0))
    return pattern.sub(replacer, text)


def render_template_for_phase(template_path: Path, phase: str, substitutions: dict[str, str]) -> str:
    """
    Render a two-phase template, stripping every '## PHASE = X' section
    whose X does not match `phase`.

    Without this, the model sees both the implement and report sections
    simultaneously.  When the implement call passes empty strings for the
    report-section placeholders (METRIC_BEFORE, VERIFIER_RESULT, …) the model
    interprets those empties as "the experiment produced no output" and follows
    the report section's output instructions — which appear *later* in context
    and therefore take precedence (recency bias).  Stripping inactive sections
    eliminates the ambiguity entirely.
    """
    text = _apply_substitutions(template_path.read_text(), substitutions)

    lines = text.splitlines(keepends=True)
    result: list[str] = []
    in_wrong_phase = False
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("## PHASE = "):
            section_phase = stripped[len("## PHASE = "):].strip()
            in_wrong_phase = (section_phase != phase)
        if not in_wrong_phase:
            result.append(line)

    joined = "".join(result)
    # Remove a trailing horizontal rule left behind by the stripped section.
    joined = re.sub(r"\n---\s*$", "\n", joined)
    return joined


_IMPLEMENTATION_AGENTS = {"implementation", "exploration"}

# Metric direction display transform (§6 pre-render calling convention)
_METRIC_DIRECTION_DISPLAY: dict[str, str] = {
    "lower_is_better":  "lower is better",
    "higher_is_better": "higher is better",
}


def _load_domain_context() -> str:
    """Pre-render domain_context.md with instance values, return rendered string.

    Substitutes {{METRIC_NAME}}, {{ARTIFACT_NAME}}, etc. before injection so
    that domain_context.md can reference these without hardcoding them.
    Raises ValueError if any {{VAR}} remains after substitution (injection vector
    defense — §6 pre-render calling convention).
    """
    dc_path = AGENT_PROMPTS_DIR / "domain_context.md"
    if not dc_path.exists():
        return ""
    inner_subs = {
        "METRIC_NAME":       METRIC_NAME,
        "METRIC_DIRECTION":  _METRIC_DIRECTION_DISPLAY.get(METRIC_DIRECTION, METRIC_DIRECTION),
        "ARTIFACT_NAME":     ARTIFACT_SNAPSHOT_NAME,
        "ARTIFACT_XML_TAG":  ARTIFACT_XML_TAG,
        "BENCHMARK_DESCRIPTION": "",  # filled from goal.md when config is available
    }
    text = _apply_substitutions(dc_path.read_text(), inner_subs)
    # Check for unresolved variables — would indicate a missing substitution or
    # an injection attempt via domain_context.md.
    unresolved = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if unresolved:
        raise ValueError(
            f"domain_context.md contains unresolved variables after pre-render: "
            f"{unresolved}. Add them to _load_domain_context() or fix domain_context.md."
        )
    return text


def _common_subs() -> dict[str, str]:
    """Return substitutions common to all agent prompts (§6)."""
    return {
        "DOMAIN_CONTEXT":    _load_domain_context(),
        "ARTIFACT_NAME":     ARTIFACT_SNAPSHOT_NAME,
        "ARTIFACT_LANGUAGE": "C++17",
        "ARTIFACT_XML_TAG":  ARTIFACT_XML_TAG,
        "METRIC_NAME":       METRIC_NAME,
        "METRIC_DIRECTION":  _METRIC_DIRECTION_DISPLAY.get(METRIC_DIRECTION, METRIC_DIRECTION),
        "BUILD_COMMAND":     "g++ -O2 -std=c++17",
        "BENCHMARK_DESCRIPTION": "DIMACS CAL road network, 5 source nodes, median runtime",
        "CORRECTNESS_CONSTRAINT": "all shortest-path distances match reference Dijkstra exactly",
        # Enhancement 1: last benchmark's PROFILE lines (empty placeholder when unavailable)
        "PROFILE_DATA":      _format_profile_data(),
    }


def call_agent(prompt: str, agent_name: str = "unknown") -> str:
    """Call the Anthropic API with CURRENT_AGENT identity in system prompt."""
    system = (
        f"CURRENT_AGENT: {agent_name}\n"
        f"You are the {agent_name} in a permission-controlled autoresearch loop. "
        f"Perform only the task described below. Do not read or modify files outside "
        f"your defined role. Output only what the instructions request."
    )
    # Implementation and exploration agents output full sssp.cpp files which can
    # be large — give them extra headroom to avoid truncated XML tags.
    max_tokens = MAX_TOKENS_IMPLEMENTATION if agent_name in _IMPLEMENTATION_AGENTS else MAX_TOKENS_DEFAULT
    try:
        # Always use streaming — the Anthropic API requires it for requests
        # that may take >10 minutes (e.g. implementation agents with max_tokens=32000).
        with _get_client().messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            text = stream.get_final_text()
            final_msg = stream.get_final_message()
            stop_reason = final_msg.stop_reason
        if stop_reason == "max_tokens":
            print(f"  [WARN] {agent_name} response TRUNCATED at max_tokens={max_tokens} "
                  f"(response_len={len(text)})")
        return text
    except anthropic.AuthenticationError as e:
        # Auth errors are permanent — stop the loop immediately.
        print(f"\n[FATAL] Authentication error: {e}")
        print("Set ANTHROPIC_API_KEY and restart.")
        sys.exit(1)
    except Exception as e:
        print(f"  [WARN] Anthropic API error ({agent_name}): {e}")
        return ""


def extract_xml_tag(text: str, tag: str) -> str:
    """Extract content between <tag> and </tag>."""
    pattern = rf"<{tag}>(.*?)</{tag}>"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def recent_results(n: int = 10) -> str:
    """Return last n lines of results.tsv (excluding header)."""
    try:
        lines = RESULTS_TSV.read_text().splitlines()
        data_lines = [l for l in lines if not l.startswith("exp_id")]
        return "\n".join(data_lines[-n:]) if data_lines else "(none yet)"
    except FileNotFoundError:
        return "(none yet)"


def _format_profile_data() -> str:
    """Format _last_profile_data as a human-readable string for agent prompts.

    Returns a placeholder string when no profile data is available, so templates
    always render cleanly (no dangling section headers).
    """
    if not _last_profile_data:
        return "(no profile data collected)"
    return "\n".join(f"{k} = {v}" for k, v in _last_profile_data.items())


def _extract_section(content: str, heading: str) -> str:
    """Extract the body of a markdown section from its heading until the next
    same-or-higher-level heading (or EOF).

    Example: _extract_section(text, "## Analysis") returns everything between
    "## Analysis\\n" and the next "## " heading or end of file.
    """
    pat = re.compile(
        r"^" + re.escape(heading) + r"\n(.*?)(?=\n## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pat.search(content)
    return m.group(1).strip() if m else ""


def _load_recent_experiment_summaries(n: int, total_experiments: int) -> str:
    """Load Analysis sections from the N most recent experiment-result.md files.

    Reads total_experiments from the caller (sourced from exploitation-progress.json)
    and iterates backwards, skipping experiments with no archived result file
    (ABANDONED, TIMEOUT, implementation failures).

    Returns a formatted string for injection into {{RECENT_EXPERIMENTS}}.
    """
    if n <= 0:
        return "(no recent experiment summaries)"

    summaries: list[str] = []
    i = total_experiments
    while i >= 1 and len(summaries) < n:
        eid = experiment_id(i)
        result_file = ARTIFACTS_DIR / eid / "experiment-result.md"
        if result_file.exists():
            content = result_file.read_text()
            # Extract ## Analysis section; fall back to full file for pre-Enhancement-2 experiments
            section = _extract_section(content, "## Analysis")
            if not section:
                section = content

            # Apply per-file line cap
            lines = section.splitlines()
            if len(lines) > RECENT_EXPERIMENTS_MAX_LINES_PER:
                lines = lines[:RECENT_EXPERIMENTS_MAX_LINES_PER]
                lines.append("...(truncated)")

            # Pull status from result file for the summary header
            status_m = re.search(r"^Status:\s*(\S+)", content, re.MULTILINE)
            status_str = status_m.group(1) if status_m else "unknown"

            summaries.append(f"### {eid} (status: {status_str})\n" + "\n".join(lines))
        i -= 1

    if not summaries:
        return "(no recent experiment summaries)"

    loaded_count = len(summaries)
    total_chars  = sum(len(s) for s in summaries)
    print(f"  [DEBUG] Loaded {loaded_count} recent experiment summary(ies) ({total_chars} chars)")
    return "\n\n".join(summaries)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def experiment_id(n: int) -> str:
    return f"exp-{n:03d}"


def exploration_id(n: int) -> str:
    return f"explore-{n:03d}"


# ---------------------------------------------------------------------------
# autoresearch.sh runner
# ---------------------------------------------------------------------------

def run_benchmark() -> dict[str, float] | None:
    """Run benchmark script, parse METRIC and PROFILE lines, return dict or None on failure.

    Side-effect: populates _last_profile_data with any PROFILE key=value lines emitted
    by the benchmark (capped at 20 lines). Cleared to {} on any failure path.
    """
    global _last_profile_data
    try:
        result = subprocess.run(
            ["bash", BENCHMARK_SCRIPT],
            cwd=INSTANCE_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        print(f"  [WARN] {BENCHMARK_SCRIPT} timed out (600s limit)")
        _last_profile_data = {}
        return None
    if result.returncode != 0:
        print(f"  [WARN] {BENCHMARK_SCRIPT} failed (rc={result.returncode})")
        print(f"  stderr: {result.stderr[:500]}")
        _last_profile_data = {}
        return None

    metrics: dict[str, float] = {}
    profile: dict[str, float] = {}
    for line in result.stdout.splitlines():
        m = re.match(r"METRIC\s+(\w+)=([0-9eE.+-]+)", line)
        if m:
            try:
                metrics[m.group(1)] = float(m.group(2))
            except ValueError:
                print(f"  [WARN] Malformed METRIC value ignored: {line.strip()}")
            continue
        if len(profile) < 20:
            p = re.match(r"PROFILE\s+(\w+)=([0-9eE.+-]+)", line)
            if p:
                try:
                    profile[p.group(1)] = float(p.group(2))
                except ValueError:
                    print(f"  [WARN] Malformed PROFILE value ignored: {line.strip()}")

    _last_profile_data = profile
    if profile:
        print(f"  [DEBUG] Parsed {len(profile)} PROFILE line(s)")

    if METRIC_NAME not in metrics:
        print(f"  [WARN] {BENCHMARK_SCRIPT} produced no METRIC {METRIC_NAME} line")
        _last_profile_data = {}
        return None
    return metrics


# ---------------------------------------------------------------------------
# Verifier (generic subprocess wrapper — §5.2)
# ---------------------------------------------------------------------------
# In Step 9 these come from instance.json config["verifier_command"] and
# config["verdict_output_path"].
VERIFIER_COMMAND: list[str] | None = None    # instance.json: verifier_command
VERDICT_OUTPUT_PATH: Path | None   = None    # instance.json: verdict_output_path


def collect_source_ids() -> list[str]:
    """Read source IDs from data/cal.ss (first 5)."""
    sources_file = DATA_DIR / "cal.ss"
    sources: list[str] = []
    try:
        for line in sources_file.read_text().splitlines():
            if line.startswith("s "):
                parts = line.split()
                if len(parts) >= 2:
                    sources.append(parts[1])
            if len(sources) == 5:
                break
    except FileNotFoundError:
        pass
    return sources


def run_verifier() -> dict[str, Any]:
    """Run the instance verifier script. Return parsed verdict dict (§5.2)."""
    # Remove stale verdict before running so a crashed verifier produces no output
    if VERDICT_OUTPUT_PATH.exists():
        VERDICT_OUTPUT_PATH.unlink()

    try:
        result = subprocess.run(
            VERIFIER_COMMAND,
            cwd=INSTANCE_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "detail": "verifier timed out"}
    except Exception as e:
        return {"status": "FAIL", "detail": f"verifier failed to start: {e}"}

    if not VERDICT_OUTPUT_PATH.exists():
        return {"status": "FAIL", "detail": "verifier produced no output"}

    try:
        return json.loads(VERDICT_OUTPUT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"status": "FAIL", "detail": "verifier produced no output"}


def verifier_passed(verdict: dict[str, Any]) -> bool:
    status = verdict.get("status", "FAIL")
    return str(status).upper() in ("PASS", "OK", "SUCCESS")


# ---------------------------------------------------------------------------
# results.tsv helpers
# ---------------------------------------------------------------------------

def save_artifact(exp_id: str, report_content: str) -> None:
    """Snapshot artifact, params.json, and experiment-result.md to artifacts/exp-N/."""
    artifact_dir = ARTIFACTS_DIR / exp_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for src, name in [
        (ARTIFACT_PATH,            ARTIFACT_SNAPSHOT_NAME),
        (PARAMS_JSON,         "params.json"),
        (PROPOSAL_MD,         "proposal.md"),
        (OBJECTIONS_MD,       "objections.md"),
        (FINAL_PROPOSAL_MD,   "final-proposal.md"),
    ]:
        try:
            shutil.copy(src, artifact_dir / name)
        except FileNotFoundError:
            pass
    if report_content:
        (artifact_dir / "experiment-result.md").write_text(report_content)


def init_results_tsv() -> None:
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(
            "exp_id\tmetric_before\tmetric_after\tdelta\tstatus\tconfidence\tidea\n"
        )


def append_results_tsv(
    exp_id: str,
    metric_before: float,
    metric_after: float | None,
    status: str,
    confidence: str,
    idea: str,
) -> None:
    if metric_after is None:
        delta = "N/A"
        after_str = "N/A"
    else:
        diff = metric_after - metric_before
        pct = (diff / metric_before * 100) if metric_before else 0
        delta = f"{pct:+.1f}%"
        after_str = str(metric_after)
    row = f"{exp_id}\t{metric_before}\t{after_str}\t{delta}\t{status}\t{confidence}\t{idea}\n"
    with RESULTS_TSV.open("a") as f:
        f.write(row)


def append_progress_log(exp_id: str, metric_before: float, metric_after: float | None, status: str) -> None:
    after_str = f"{metric_after}ms" if metric_after is not None else "N/A"
    line = f"{now_iso()} | {exp_id} | {metric_before}ms → {after_str} | {status}\n"
    with PROGRESS_LOG.open("a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# master.json + exploitation-progress.json
# ---------------------------------------------------------------------------

def update_master(
    metric_value: float,
    description: str,
    exp_id: str,
) -> None:
    master = {
        "metric_name": METRIC_NAME,
        "metric_value": metric_value,
        "hash": exp_id,
        "description": description,
        "platform": socket.gethostname(),
        "promoted_at": now_iso(),
        "experiment_id": exp_id,
    }
    write_json(MASTER_JSON, master)


def load_progress() -> dict[str, Any]:
    data = read_json(EXPLOITATION_PROGRESS)
    defaults: dict[str, Any] = {
        "runs_since_last_explore": 0,
        "runs_on_current_path": 0,
        "current_master_metric": 0.0,
        "gain_rate_last_10": 0.0,
        "early_session_gain_rate": 0.0,
        "explore_every_N":    SCHEDULE_EXPLORE_EVERY_N,
        "explore_budget":     SCHEDULE_EXPLORE_BUDGET,
        "total_experiments":  0,
        "max_debate_rounds":  SCHEDULE_MAX_DEBATE_ROUNDS,
        "total_scouts":       0,
    }
    for k, v in defaults.items():
        data.setdefault(k, v)
    return data


def save_progress(prog: dict[str, Any]) -> None:
    write_json(EXPLOITATION_PROGRESS, prog)


# ---------------------------------------------------------------------------
# Debate cycle
# ---------------------------------------------------------------------------

def _parse_cited_ids(proposal_content: str) -> list[str]:
    """Extract idea IDs from the 'Ideas cited:' line of a proposal."""
    for line in proposal_content.splitlines():
        if line.strip().lower().startswith("ideas cited:"):
            raw = line.split(":", 1)[1].strip()
            if raw.upper() == "NONE" or not raw:
                return []
            return [tok.strip() for tok in raw.split(",") if re.match(r"idea-\d+", tok.strip())]
    return []


def run_debate(master: dict, progress: dict) -> tuple[str, str, list[str]]:
    """
    Run debate between Researcher A and B.
    Returns (final_proposal_md_content, confidence_string, cited_idea_ids).
    Returns ("ABANDONED", "0", []) if FATAL.
    """
    max_rounds = progress.get("max_debate_rounds", 2)
    goal_md = read_file(GOAL_MD)
    references_md = read_file(REFERENCES_MD)
    do_not_repeat = read_file(DO_NOT_REPEAT_MD, "(empty)")
    recent = recent_results()
    current_params = read_file(PARAMS_JSON)
    current_exp = progress.get("total_experiments", 0)
    total_experiments = progress.get("total_experiments", 0)

    # Load idea window from MBMM (scored + epsilon-greedy explored)
    idea_window, window_ids = mbmm.load_idea_window(current_exp, total_experiments)
    idea_window_text = mbmm.format_window_for_prompt(idea_window, current_exp, total_experiments)
    mbmm.mark_ideas_shown(window_ids, current_exp)

    # Enhancement 3: load recent experiment summaries once per debate round (not per retry)
    recent_exp_summaries = _load_recent_experiment_summaries(RECENT_EXPERIMENTS_WINDOW, total_experiments)

    proposal_content = ""
    for round_n in range(1, max_rounds + 1):
        if round_n == 1:
            # Researcher A produces initial proposal
            prompt = render_template(AGENT_PROMPTS_DIR / "researcher_a.md", {
                **_common_subs(),
                "MASTER_JSON": json.dumps(master, indent=2),
                "RECENT_RESULTS": recent,
                "IDEA_WINDOW": idea_window_text,
                "DO_NOT_REPEAT": do_not_repeat,
                "GOAL_MD": goal_md,
                "REFERENCES_MD": references_md,
                "CURRENT_PARAMS": current_params,
                "RECENT_EXPERIMENTS": recent_exp_summaries,
            })
            proposal_content = call_agent(prompt, "researcher-a")
            guarded_write(PROPOSAL_MD, proposal_content, "researcher-a")
            first_line = proposal_content.splitlines()[0] if proposal_content else "(no proposal)"
            print(f"  Researcher A: {first_line}")
        else:
            # Researcher A refines after CHALLENGED
            prompt = render_template(AGENT_PROMPTS_DIR / "researcher_a.md", {
                **_common_subs(),
                "MASTER_JSON": json.dumps(master, indent=2),
                "RECENT_RESULTS": recent,
                "IDEA_WINDOW": idea_window_text,
                "DO_NOT_REPEAT": do_not_repeat,
                "GOAL_MD": goal_md,
                "REFERENCES_MD": references_md,
                "CURRENT_PARAMS": current_params,
                "RECENT_EXPERIMENTS": recent_exp_summaries,
            })
            # Append objections context to prompt for refinement
            objections_context = f"\n\n---\n## Previous objections (round {round_n-1})\n{read_file(OBJECTIONS_MD)}\n\nRevise your proposal to address the objections above."
            proposal_content = call_agent(prompt + objections_context, "researcher-a")
            guarded_write(PROPOSAL_MD, proposal_content, "researcher-a")
            first_line = proposal_content.splitlines()[0] if proposal_content else "(no proposal)"
            print(f"  Researcher A (refined): {first_line}")

        # Researcher B reviews
        b_prompt = render_template(AGENT_PROMPTS_DIR / "researcher_b.md", {
            **_common_subs(),
            "PROPOSAL_MD": proposal_content,
            "ROUND_N": str(round_n),
            "MAX_ROUNDS": str(max_rounds),
        })
        objections_content = call_agent(b_prompt, "researcher-b")
        guarded_write(OBJECTIONS_MD, objections_content, "researcher-b")

        # Parse status
        status_match = re.search(r"Status:\s*(APPROVED|FATAL|CHALLENGED)", objections_content)
        status = status_match.group(1) if status_match else "CHALLENGED"
        print(f"  Researcher B: {status}")

        if status == "FATAL":
            return "ABANDONED", "0", []

        if status == "APPROVED":
            confidence = "HIGH" if round_n == 1 else "MEDIUM"
            return proposal_content, confidence, _parse_cited_ids(proposal_content)

        # CHALLENGED — loop (or cap)
        if round_n == max_rounds:
            print(f"  Debate capped at round {max_rounds} — proceeding with MEDIUM confidence")
            return proposal_content, "MEDIUM", _parse_cited_ids(proposal_content)

    return proposal_content, "MEDIUM", _parse_cited_ids(proposal_content)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

def call_attribution(input_type: str, input_content: str, current_exp: int = 0) -> None:
    # Pass ALL active ideas to the recorder but in a slim format (id + title +
    # mechanism only).  This lets the recorder signal any idea in the store —
    # including buried ones that would otherwise be invisible — without the token
    # cost of the full schema.  The recorder only needs enough context to
    # recognise which ideas relate to the current result.
    all_ideas = mbmm.load_store()
    slim_ideas = [
        {"id": i["id"], "title": i["title"], "mechanism": i["mechanism"]}
        for i in all_ideas
        if i.get("status", "active") == "active"
    ]
    ideas_compact = json.dumps(slim_ideas, indent=2)

    prompt = render_template(AGENT_PROMPTS_DIR / "attribution.md", {
        "INPUT_TYPE":     input_type,
        "INPUT_CONTENT":  input_content,
        "CURRENT_IDEAS":  ideas_compact,
    })
    response = call_agent(prompt, "attribution")

    # --- Parse MBMM signals ---
    signals_raw  = extract_xml_tag(response, "idea_signals")
    new_idea_raw = extract_xml_tag(response, "new_idea")

    signals:     list[dict] = []
    new_entries: list[dict] = []

    if signals_raw and signals_raw.strip().upper() != "NONE":
        try:
            signals = json.loads(signals_raw)
            if not isinstance(signals, list):
                signals = [signals]
        except json.JSONDecodeError:
            print("  [WARN] Recorder returned invalid JSON for <idea_signals>")

    if new_idea_raw and new_idea_raw.strip().upper() != "NONE":
        try:
            entry = json.loads(new_idea_raw)
            new_entries = [entry] if isinstance(entry, dict) else entry
        except json.JSONDecodeError:
            print("  [WARN] Recorder returned invalid JSON for <new_idea>")

    if signals or new_entries:
        result = mbmm.apply_signals(signals, new_entries, current_exp)
        if result["ideas_added"]:
            print(f"  Recorder: → idea-store.json +{result['ideas_added']} idea(s)")
        if result["signals_applied"]:
            print(f"  Recorder: → {result['signals_applied']} signal(s) applied")

    # --- do-not-repeat.md is unchanged ---
    do_not_repeat_entry = extract_xml_tag(response, "do_not_repeat_entry")
    if do_not_repeat_entry and do_not_repeat_entry.upper() != "NONE":
        guarded_append(DO_NOT_REPEAT_MD, "\n\n" + do_not_repeat_entry, "attribution")
        print("  Recorder: → do-not-repeat.md updated")


# ---------------------------------------------------------------------------
# Implementation cycle
# ---------------------------------------------------------------------------

def run_implementation(
    final_proposal: str,
    exp_id: str,
    metric_before: float,
    sssp_cpp_backup: str,
    params_json_backup: str,
) -> tuple[float | None, dict, str]:
    """
    Implement the proposal, run benchmark, run verifier.
    Returns (metric_after, verdict, experiment_result_content).
    Restores backup on error.
    """
    # Phase: implement — use phase-specific rendering to strip the report section
    prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "implementation.md", "implement", {
        **_common_subs(),
        "PHASE": "implement",
        "FINAL_PROPOSAL_MD": final_proposal,
        "CURRENT_ARTIFACT": sssp_cpp_backup,
        "CURRENT_PARAMS_JSON": params_json_backup,
        "METRIC_BEFORE": "",
        "METRIC_AFTER": "",
        "VERIFIER_RESULT": "",
        "EXPERIMENT_ID": exp_id,
    })
    impl_response = call_agent(prompt, "implementation")

    new_cpp = extract_xml_tag(impl_response, ARTIFACT_XML_TAG)
    new_params = extract_xml_tag(impl_response, "params_json")

    if not new_cpp:
        resp_len = len(impl_response) if impl_response else 0
        print(f"  [WARN] Implementation agent returned no <{ARTIFACT_XML_TAG}> block — aborting experiment")
        print(f"  [DEBUG] Response length: {resp_len} chars")
        if impl_response:
            # Show first 500 chars and last 200 chars to catch both preamble and truncation
            head = impl_response[:500].replace("\n", "↵")
            tail = impl_response[-200:].replace("\n", "↵") if resp_len > 500 else ""
            print(f"  [DEBUG] Response head: {head}")
            if tail:
                print(f"  [DEBUG] Response tail: {tail}")
        else:
            print(f"  [DEBUG] Response is empty")
        return None, {"status": "FAIL", "detail": "no code produced"}, ""

    guarded_write(ARTIFACT_PATH, new_cpp, "implementation")
    if new_params:
        guarded_write(PARAMS_JSON, new_params, "implementation")

    # Benchmark
    metrics = run_benchmark()
    metric_after = metrics[METRIC_NAME] if metrics else None
    if metric_after is not None:
        print(f"  Benchmark: {metric_after}ms (was {metric_before}ms)")
    else:
        print("  Benchmark: FAILED")

    # Verifier
    verdict = run_verifier()
    v_pass = verifier_passed(verdict)
    print(f"  Verifier: {'PASS' if v_pass else 'FAIL'}")

    # Phase: report
    report_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "implementation.md", "report", {
        **_common_subs(),
        "PHASE": "report",
        "FINAL_PROPOSAL_MD": final_proposal,
        "METRIC_BEFORE": str(metric_before),
        "METRIC_AFTER": str(metric_after) if metric_after is not None else "N/A",
        "VERIFIER_RESULT": json.dumps(verdict, indent=2),
        "EXPERIMENT_ID": exp_id,
        "CURRENT_ARTIFACT": "",
        "CURRENT_PARAMS_JSON": "",
    })
    report_content = call_agent(report_prompt, "implementation")
    guarded_write(EXPERIMENT_RESULT_MD, report_content, "implementation")

    return metric_after, verdict, report_content


# ---------------------------------------------------------------------------
# Exploration cycle
# ---------------------------------------------------------------------------

def run_exploration(master: dict, progress: dict, explore_n: int) -> None:
    eid = exploration_id(explore_n)
    print(f"\n--- {eid} ---")
    goal_md = read_file(GOAL_MD)
    references_md = read_file(REFERENCES_MD)
    unexplored = read_file(UNEXPLORED_IDEAS_MD, "(empty)")
    do_not_repeat = read_file(DO_NOT_REPEAT_MD, "(empty)")
    recent = recent_results()
    explore_budget = progress.get("explore_budget", 2)

    # Phase: plan
    plan_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "exploration.md", "plan", {
        **_common_subs(),
        "PHASE": "plan",
        "UNEXPLORED_IDEAS": unexplored,
        "DO_NOT_REPEAT": do_not_repeat,
        "MASTER_JSON": json.dumps(master, indent=2),
        "RECENT_RESULTS": recent,
        "GOAL_MD": goal_md,
        "REFERENCES_MD": references_md,
        "EXPLORE_BUDGET": str(explore_budget),
        "EXPLORE_ID": eid,
        "EXPLORATION_PLAN": "",
        "RUN_RESULTS": "",
    })
    plan_response = call_agent(plan_prompt, "exploration")
    selected_idea_xml = extract_xml_tag(plan_response, "selected_idea")
    if not selected_idea_xml:
        print(f"  [WARN] Exploration agent returned no <selected_idea> — skipping")
        return

    exploration_plan_xml = f"<selected_idea>{selected_idea_xml}</selected_idea>"
    title_m = re.search(r"<title>(.*?)</title>", selected_idea_xml)
    title = title_m.group(1).strip() if title_m else eid
    print(f"  Exploring: {title}")

    # Extract run elements
    run_elements = re.findall(r'<run\s+n="(\d+)">(.*?)</run>', selected_idea_xml, re.DOTALL)
    run_results: list[dict] = []
    best_metric: float | None = None
    best_cpp: str | None = None
    best_params: str | None = None

    sssp_cpp_base = read_file(ARTIFACT_PATH)
    params_base = read_file(PARAMS_JSON)

    for run_n_str, run_body in run_elements[:explore_budget]:
        run_n = int(run_n_str)
        change_m = re.search(r"<change>(.*?)</change>", run_body, re.DOTALL)
        change_desc = change_m.group(1).strip() if change_m else "(no change specified)"

        print(f"  Run {run_n}: {change_desc[:80]}")

        # Always start from current master for each run
        write_file(ARTIFACT_PATH, sssp_cpp_base)
        write_file(PARAMS_JSON, params_base)

        # Use implementation agent to apply the change
        mini_proposal = f"Idea: {change_desc}\nMechanism: Exploration run {run_n} for {eid}.\nExpected signal: unknown — characterizing search space.\nConditions assumed:\n  - N/A\nKnown risks:\n  - N/A\nNot in do-not-repeat because: exploration run"
        impl_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "implementation.md", "implement", {
            **_common_subs(),
            "PHASE": "implement",
            "FINAL_PROPOSAL_MD": mini_proposal,
            "CURRENT_ARTIFACT": sssp_cpp_base,
            "CURRENT_PARAMS_JSON": params_base,
            "METRIC_BEFORE": "",
            "METRIC_AFTER": "",
            "VERIFIER_RESULT": "",
            "EXPERIMENT_ID": f"{eid}-run{run_n}",
        })
        impl_response = call_agent(impl_prompt, "implementation")
        new_cpp = extract_xml_tag(impl_response, ARTIFACT_XML_TAG)
        new_params = extract_xml_tag(impl_response, "params_json")

        if new_cpp:
            guarded_write(ARTIFACT_PATH, new_cpp, "exploration")
        if new_params:
            guarded_write(PARAMS_JSON, new_params, "exploration")

        metrics = run_benchmark()
        metric_after = metrics[METRIC_NAME] if metrics else None
        verdict = run_verifier() if metric_after is not None else {"status": "FAIL", "detail": "benchmark failed"}
        v_pass = verifier_passed(verdict)

        run_result: dict = {
            "run_n": run_n,
            "metric_before": master.get("metric_value", 0.0),
            "metric_after": metric_after,
            "verifier_status": "PASS" if v_pass else "FAIL",
        }
        if not v_pass:
            run_result["verifier_detail"] = verdict.get("detail", "")
        run_results.append(run_result)

        if metric_after is not None and v_pass:
            if best_metric is None or is_improvement(best_metric, metric_after):
                best_metric = metric_after
                best_cpp = new_cpp or sssp_cpp_base
                best_params = new_params or params_base

        print(f"    → {metric_after}ms, verifier: {'PASS' if v_pass else 'FAIL'}")

        # Always restore backup after each run
        write_file(ARTIFACT_PATH, sssp_cpp_base)
        write_file(PARAMS_JSON, params_base)

    # Phase: report
    report_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "exploration.md", "report", {
        **_common_subs(),
        "PHASE": "report",
        "EXPLORATION_PLAN": exploration_plan_xml,
        "RUN_RESULTS": json.dumps(run_results, indent=2),
        "EXPLORE_ID": eid,
        "UNEXPLORED_IDEAS": "",
        "DO_NOT_REPEAT": "",
        "MASTER_JSON": "",
        "RECENT_RESULTS": "",
        "GOAL_MD": "",
        "REFERENCES_MD": "",
        "EXPLORE_BUDGET": str(explore_budget),
    })
    report_content = call_agent(report_prompt, "exploration")
    guarded_write(EXPLORATION_RESULT_MD, report_content, "exploration")

    # Promote to master if any run beat it
    master_metric = master.get("metric_value", float("inf"))
    if best_metric is not None and is_improvement(master_metric, best_metric):
        print(f"  → Exploration found improvement: {best_metric}ms (was {master_metric}ms) — promoting to master")
        update_master(best_metric, f"Promoted from {eid}: {title}", eid)
        if best_cpp:
            write_file(ARTIFACT_PATH, best_cpp)
        if best_params:
            write_file(PARAMS_JSON, best_params)

    explore_current_exp = progress.get("total_experiments", 0)
    call_attribution("exploration", report_content, current_exp=explore_current_exp)


# ---------------------------------------------------------------------------
# One experiment
# ---------------------------------------------------------------------------

def run_one_experiment(exp_n: int, progress: dict) -> dict:
    """Run one full experiment. Returns updated progress dict."""
    eid = experiment_id(exp_n)
    print(f"\n=== {eid} ===")

    master = read_json(MASTER_JSON)
    metric_before = master.get("metric_value", 0.0)

    # Backup current code in memory
    sssp_cpp_backup = read_file(ARTIFACT_PATH)
    params_json_backup = read_file(PARAMS_JSON)

    # --- Debate ---
    final_proposal, confidence, cited_ids = run_debate(master, progress)

    if final_proposal == "ABANDONED":
        write_file(FINAL_PROPOSAL_MD, "ABANDONED")
        print(f"  → ABANDONED (FATAL objection)")
        call_attribution("experiment", "Status: FATAL — proposal abandoned before implementation", current_exp=exp_n)
        append_results_tsv(eid, metric_before, None, "abandoned", "0", "FATAL")
        append_progress_log(eid, metric_before, None, "abandoned")
        progress["total_experiments"] = progress.get("total_experiments", 0) + 1
        progress["runs_since_last_explore"] = progress.get("runs_since_last_explore", 0) + 1
        return progress

    write_file(FINAL_PROPOSAL_MD, final_proposal)

    # --- Implementation + benchmark + verify ---
    try:
        metric_after, verdict, report_content = run_implementation(
            final_proposal, eid, metric_before, sssp_cpp_backup, params_json_backup
        )
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Benchmark timed out — restoring backup")
        write_file(ARTIFACT_PATH, sssp_cpp_backup)
        write_file(PARAMS_JSON, params_json_backup)
        append_results_tsv(eid, metric_before, None, "discarded", confidence, "TIMEOUT")
        append_progress_log(eid, metric_before, None, "discarded")
        if cited_ids:
            mbmm.record_citation_outcome(cited_ids, kept=False, fail_type="TIMEOUT", current_exp=exp_n)
        progress["total_experiments"] = progress.get("total_experiments", 0) + 1
        progress["runs_since_last_explore"] = progress.get("runs_since_last_explore", 0) + 1
        save_progress(progress)
        return progress

    # Snapshot artifact before any keep/discard restore
    save_artifact(eid, report_content)

    v_pass = verifier_passed(verdict)
    improved = (
        metric_after is not None
        and metric_before > 0
        and is_improvement(metric_before, metric_after)
    )

    # --- Keep or discard ---
    if v_pass and improved:
        pct_str = format_delta(metric_before, metric_after)
        print(f"  → KEPT ({pct_str})")
        idea_line = ""
        for line in final_proposal.splitlines():
            if line.startswith("Idea:"):
                idea_line = line[5:].strip()
                break
        update_master(metric_after, idea_line or eid, eid)
        append_results_tsv(eid, metric_before, metric_after, "kept", confidence, idea_line)
        append_progress_log(eid, metric_before, metric_after, "kept")
        progress["runs_on_current_path"] = progress.get("runs_on_current_path", 0) + 1
        progress["current_master_metric"] = metric_after
        if cited_ids:
            res = mbmm.record_citation_outcome(cited_ids, kept=True, fail_type="", current_exp=exp_n)
            print(f"  Citation outcome recorded for {res['outcomes_recorded']} idea(s): {cited_ids}")
    else:
        # Restore backup
        write_file(ARTIFACT_PATH, sssp_cpp_backup)
        write_file(PARAMS_JSON, params_json_backup)
        reason = "FAIL-VERIFIER" if not v_pass else "FAIL-METRIC"
        pct_str = ""
        if metric_after is not None and metric_before > 0:
            pct_str = f" ({format_delta(metric_before, metric_after)})"
        print(f"  → DISCARDED ({reason}{pct_str})")
        append_results_tsv(eid, metric_before, metric_after, "discarded", confidence,
                           "FAILED: " + reason)
        append_progress_log(eid, metric_before, metric_after, "discarded")
        if cited_ids:
            res = mbmm.record_citation_outcome(cited_ids, kept=False, fail_type=reason, current_exp=exp_n)
            print(f"  Citation outcome recorded for {res['outcomes_recorded']} idea(s): {cited_ids}")
        if report_content:
            call_attribution("experiment", report_content, current_exp=exp_n)

    progress["total_experiments"] = progress.get("total_experiments", 0) + 1
    progress["runs_since_last_explore"] = progress.get("runs_since_last_explore", 0) + 1
    save_progress(progress)
    return progress


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def cmd_setup() -> None:
    _assert_instance_configured()
    print("=== Autoresearch Setup ===")

    # Validate required files
    required = [GOAL_MD, ARTIFACT_PATH, PARAMS_JSON]
    for f in required:
        if not f.exists():
            print(f"  [ERROR] Required file missing: {f}")
            sys.exit(1)

    # Ensure context dir and references.md exist
    REFERENCES_MD.parent.mkdir(parents=True, exist_ok=True)
    if not REFERENCES_MD.exists():
        REFERENCES_MD.write_text("(no references yet)\n")

    # Ensure unexplored-ideas.md and do-not-repeat.md exist
    if not UNEXPLORED_IDEAS_MD.exists():
        UNEXPLORED_IDEAS_MD.write_text("# Unexplored Ideas\n\n(none yet)\n")
    if not DO_NOT_REPEAT_MD.exists():
        DO_NOT_REPEAT_MD.write_text("# Do Not Repeat\n\n(none yet)\n")

    print("Running baseline benchmark…")
    metrics = run_benchmark()
    if metrics is None:
        print("  [ERROR] Baseline benchmark failed — check autoresearch.sh")
        sys.exit(1)

    baseline_ms = metrics[METRIC_NAME]
    print(f"  Baseline {METRIC_NAME} = {baseline_ms}")

    # Update goal.md baseline line
    goal_text = GOAL_MD.read_text()
    goal_text = re.sub(
        r"Baseline: TBD.*",
        f"Baseline: {baseline_ms} ms (measured {now_iso()})",
        goal_text,
    )
    write_file(GOAL_MD, goal_text)

    # Write master.json
    update_master(baseline_ms, "baseline (setup)", "exp-000")

    # Write results.tsv header
    init_results_tsv()
    append_results_tsv("exp-000", 0.0, baseline_ms, "baseline", "N/A", "initial benchmark")

    # Write exploitation-progress.json
    # Read session config from goal.md
    explore_every_n = 5
    explore_budget = 2
    max_debate_rounds = 2
    scout_every_m = 3
    for line in GOAL_MD.read_text().splitlines():
        m = re.match(r"\s*explore_every_N:\s*(\d+)", line)
        if m:
            explore_every_n = int(m.group(1))
        m = re.match(r"\s*explore_budget:\s*(\d+)", line)
        if m:
            explore_budget = int(m.group(1))
        m = re.match(r"\s*max_debate_rounds:\s*(\d+)", line)
        if m:
            max_debate_rounds = int(m.group(1))
        m = re.match(r"\s*scout_every_M:\s*(\d+)", line)
        if m:
            scout_every_m = int(m.group(1))

    prog = {
        "runs_since_last_explore": 0,
        "runs_on_current_path": 0,
        "current_master_metric": baseline_ms,
        "gain_rate_last_10": 0.0,
        "early_session_gain_rate": 0.0,
        "explore_every_N": explore_every_n,
        "explore_budget": explore_budget,
        "total_experiments": 0,
        "max_debate_rounds": max_debate_rounds,
        "total_scouts": 0,
    }
    save_progress(prog)

    print(f"Setup complete. Baseline = {baseline_ms} ms.")
    print(f"  explore_every_N={explore_every_n}, explore_budget={explore_budget}, max_debate_rounds={max_debate_rounds}")
    print("Run `python3 framework/core/control_plane.py --instance-dir <instance-dir>` to start the experiment loop.")


# ---------------------------------------------------------------------------
# Literature Scout
# ---------------------------------------------------------------------------

def fetch_arxiv(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Fetch papers from arXiv API for a search query."""
    params = urllib.parse.urlencode({
        "search_query": f"(cat:cs.DS OR cat:cs.DM OR cat:cs.CG) AND all:{query}",
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            content = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [WARN] arXiv fetch failed for '{query}': {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f"  [WARN] arXiv XML parse error: {e}")
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        try:
            title = (entry.find("atom:title", ns).text or "").strip().replace("\n", " ")
            abstract = (entry.find("atom:summary", ns).text or "").strip().replace("\n", " ")[:600]
            link = (entry.find("atom:id", ns).text or "").strip()
            authors = [
                (a.find("atom:name", ns).text or "")
                for a in entry.findall("atom:author", ns)
            ][:3]
            published = (entry.find("atom:published", ns).text or "")[:10]
            papers.append({
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "link": link,
                "published": published,
            })
        except Exception:
            continue
    return papers


def run_literature_scout(progress: dict, scout_n: int) -> None:
    """
    Two-phase scout:
      Phase 1 (queries)  — agent generates targeted arXiv queries from experiment state
      Phase 2 (analyze)  — agent filters fetched papers and extracts experiments
    """
    sid = f"scout-{scout_n:03d}"
    print(f"\n--- {sid} ---")

    master = read_json(MASTER_JSON)
    existing_refs = read_file(REFERENCES_MD, "(empty)")
    goal_md = read_file(GOAL_MD)
    recent = recent_results()

    shared_ctx = {
        **_common_subs(),
        "GOAL_MD":             goal_md,
        "MASTER_JSON":         json.dumps(master, indent=2),
        "RECENT_RESULTS":      recent,
        "EXISTING_REFERENCES": existing_refs,
    }

    # ------------------------------------------------------------------
    # Phase 1: generate targeted queries
    # ------------------------------------------------------------------
    query_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "literature_scout.md", "queries", {
        **shared_ctx,
        "PHASE":          "queries",
        "SEARCH_QUERIES": "",
        "FETCHED_PAPERS": "",
        "DO_NOT_REPEAT":  "",
    })
    query_response = call_agent(query_prompt, "literature-scout")

    # Parse <search_queries> block
    dynamic_queries: list[str] = []
    queries_xml = extract_xml_tag(query_response, "search_queries")
    if queries_xml:
        for m in re.finditer(r"<query[^>]*>(.*?)</query>", queries_xml, re.DOTALL):
            q = m.group(1).strip()
            if q:
                dynamic_queries.append(q)

    # Fall back to static queries if agent returned nothing
    queries_to_use = dynamic_queries if dynamic_queries else FALLBACK_QUERIES
    print(f"  Queries ({len(queries_to_use)}): {queries_to_use}")

    # ------------------------------------------------------------------
    # Fetch papers from arXiv using dynamic queries
    # ------------------------------------------------------------------
    seen_links: set[str] = set()
    all_papers: list[dict] = []
    for i, query in enumerate(queries_to_use):
        if i > 0:
            time.sleep(4)  # arXiv rate limit: ~1 req/3s
        for paper in fetch_arxiv(query, max_results=4):
            if paper["link"] not in seen_links:
                seen_links.add(paper["link"])
                all_papers.append(paper)

    if not all_papers:
        print(f"  [WARN] No papers fetched — skipping {sid}")
        return

    print(f"  Fetched {len(all_papers)} papers")

    # ------------------------------------------------------------------
    # Phase 2: analyze papers for relevance and extract experiments
    # ------------------------------------------------------------------
    analyze_prompt = render_template_for_phase(AGENT_PROMPTS_DIR / "literature_scout.md", "analyze", {
        **shared_ctx,
        "PHASE":          "analyze",
        "SEARCH_QUERIES": ", ".join(f'"{q}"' for q in queries_to_use),
        "FETCHED_PAPERS": json.dumps(all_papers, indent=2),
        "DO_NOT_REPEAT":  read_file(DO_NOT_REPEAT_MD, "(none yet)"),
    })
    response = call_agent(analyze_prompt, "literature-scout")

    if "<no_new_references/>" in response:
        print(f"  Scout found no relevant new papers — references.md unchanged")
        return

    new_refs = extract_xml_tag(response, "new_references")
    if not new_refs or not new_refs.strip():
        print(f"  [WARN] Scout returned no parseable output")
        return

    divider = f"\n\n<!-- scout-{scout_n:03d}: added {datetime.now(timezone.utc).strftime('%Y-%m-%d')} -->\n"
    guarded_append(REFERENCES_MD, divider + new_refs.strip() + "\n", "literature-scout")

    n_new = new_refs.count("### ")
    print(f"  Scout filed {n_new} new reference(s) → context/references.md")

    # Parse scout-suggested ideas into idea-store.json (literature-backed, score=0.8)
    new_ideas_raw = extract_xml_tag(response, "new_ideas")
    if new_ideas_raw and new_ideas_raw.strip().upper() != "NONE":
        try:
            scout_entries = json.loads(new_ideas_raw)
            if isinstance(scout_entries, dict):
                scout_entries = [scout_entries]
            # Force source and initial_score for scout entries
            for e in scout_entries:
                e["source"]        = "scout"
                e["initial_score"] = mbmm.INITIAL_SCORE_SCOUT
            if scout_entries:
                res = mbmm.apply_signals([], scout_entries, progress.get("total_experiments", 0))
                print(f"  Scout filed {res['ideas_added']} idea(s) → idea-store.json")
        except (json.JSONDecodeError, TypeError) as e:
            print(f"  [WARN] Scout returned invalid JSON for <new_ideas>: {e}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def cmd_run(run_once: bool = False) -> None:
    _assert_instance_configured()
    if not MASTER_JSON.exists():
        print("[ERROR] master.json not found — run --setup first")
        sys.exit(1)

    init_results_tsv()
    progress = load_progress()
    explore_every_n = progress.get("explore_every_N", 5)
    explore_counter = 0  # tracks total exploration cycles for IDs

    # Always sync current_master_metric from master.json at startup —
    # explorations promote there directly without updating progress.json.
    _startup_master = read_json(MASTER_JSON)
    progress["current_master_metric"] = _startup_master.get("metric_value",
                                            progress.get("current_master_metric", 0.0))
    save_progress(progress)

    # Migrate flat unexplored-ideas.md → idea-store.json if not yet done.
    if not mbmm.IDEA_STORE_PATH.exists() and UNEXPLORED_IDEAS_MD.exists():
        try:
            n = mbmm.migrate_from_flat_file(UNEXPLORED_IDEAS_MD)
            print(f"[MBMM] Migrated {n} ideas from unexplored-ideas.md → idea-store.json")
        except Exception as e:
            print(f"[MBMM][WARN] Migration failed: {e} — starting with empty idea store")

    print(f"Starting autoresearch loop (explore_every_N={explore_every_n})")
    print(f"Current master: {progress['current_master_metric']} ms | total_experiments: {progress.get('total_experiments')}")

    try:
        while True:
            exp_n = progress.get("total_experiments", 0) + 1
            progress = run_one_experiment(exp_n, progress)

            # Periodic GC — archive stale ideas every 10 experiments
            if progress.get("total_experiments", 0) % 10 == 0:
                n_archived = mbmm.run_gc(progress.get("total_experiments", 0), progress.get("total_experiments", 0))
                if n_archived:
                    print(f"[MBMM] GC archived {n_archived} stale idea(s)")

            # Exploration trigger
            if progress.get("runs_since_last_explore", 0) >= explore_every_n:
                explore_counter += 1

                # Literature Scout fires BEFORE every exploration
                # so the exploration agent has fresh literature in its arsenal
                progress["total_scouts"] = progress.get("total_scouts", 0) + 1
                run_literature_scout(progress, progress["total_scouts"])

                master = read_json(MASTER_JSON)
                run_exploration(master, progress, explore_counter)
                progress["runs_since_last_explore"] = 0
                # Sync current_master_metric in case exploration promoted a new best
                updated_master = read_json(MASTER_JSON)
                progress["current_master_metric"] = updated_master.get("metric_value",
                                                        progress["current_master_metric"])

                save_progress(progress)

            if run_once:
                print("\nDone (--once)")
                break

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress saved.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Instance config loading and validation (§4, §9)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "artifact", "artifact_snapshot_name", "artifact_xml_tag", "params",
    "benchmark", "verifier_command", "verdict_output_path",
    "metric_name", "metric_direction", "fallback_queries",
]
_OPTIONAL_FIELDS_WITH_DEFAULTS = {
    "artifact_language":      "",
    "build_command":          "",
    "benchmark_description":  "",
    "correctness_constraint": "",
    "model":                  "claude-opus-4-6",
    "max_tokens_implementation": 32000,
    "max_tokens_default":        8192,
    "hard_blocked":           [],
    "schedule": {
        "explore_every_N":   5,
        "explore_budget":    2,
        "max_debate_rounds": 2,
        "scout_every_N":     10,
    },
}
_VALID_DIRECTIONS = {"lower_is_better", "higher_is_better"}


def validate_instance_config(config: dict[str, Any]) -> None:
    """Validate instance.json. Raises ValueError on missing required fields.

    Warns (does not raise) on missing optional fields — they get defaults.
    Raises on unknown metric_direction values.
    """
    missing = [f for f in _REQUIRED_FIELDS if f not in config]
    if missing:
        raise ValueError(
            f"instance.json is missing required fields: {missing}"
        )
    direction = config.get("metric_direction", "")
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"instance.json: metric_direction={direction!r} is invalid. "
            f"Must be one of: {sorted(_VALID_DIRECTIONS)}"
        )
    for field, default in _OPTIONAL_FIELDS_WITH_DEFAULTS.items():
        if field not in config:
            print(f"  [WARN] instance.json missing optional field '{field}' — using default: {default!r}")


def load_instance_config(instance_dir: Path) -> dict[str, Any]:
    """Load and validate instance.json, apply defaults for optional fields."""
    config_path = instance_dir / "instance.json"
    if not config_path.exists():
        raise FileNotFoundError(f"instance.json not found in {instance_dir}")
    config = json.loads(config_path.read_text())
    validate_instance_config(config)
    for field, default in _OPTIONAL_FIELDS_WITH_DEFAULTS.items():
        config.setdefault(field, default)
    return config


def apply_instance_config(config: dict[str, Any], instance_dir: Path) -> None:
    """Update module-level constants from instance config.

    Called once at startup after load_instance_config(). This is the single
    place where all configuration flows in from instance.json.
    """
    global MODEL, ARTIFACT_XML_TAG, ARTIFACT_SNAPSHOT_NAME, METRIC_NAME
    global METRIC_DIRECTION, BENCHMARK_SCRIPT, FALLBACK_QUERIES
    global MAX_TOKENS_IMPLEMENTATION, MAX_TOKENS_DEFAULT
    global VERIFIER_COMMAND, VERDICT_OUTPUT_PATH
    global SCHEDULE_EXPLORE_EVERY_N, SCHEDULE_EXPLORE_BUDGET
    global SCHEDULE_MAX_DEBATE_ROUNDS, SCHEDULE_SCOUT_EVERY_N
    global ARTIFACT_PATH, HARD_BLOCKED, WRITE_PERMISSIONS
    global _last_profile_data, RECENT_EXPERIMENTS_WINDOW, RECENT_EXPERIMENTS_MAX_LINES_PER

    # Redirect all instance-dir-based path globals to instance_dir
    _set_instance_paths(instance_dir)

    MODEL                      = config.get("model", MODEL)
    ARTIFACT_XML_TAG           = config["artifact_xml_tag"]
    ARTIFACT_SNAPSHOT_NAME     = config["artifact_snapshot_name"]
    METRIC_NAME                = config["metric_name"]
    METRIC_DIRECTION           = config["metric_direction"]
    BENCHMARK_SCRIPT           = config["benchmark"]
    FALLBACK_QUERIES           = config["fallback_queries"]
    MAX_TOKENS_IMPLEMENTATION  = config.get("max_tokens_implementation", MAX_TOKENS_IMPLEMENTATION)
    MAX_TOKENS_DEFAULT         = config.get("max_tokens_default", MAX_TOKENS_DEFAULT)
    VERIFIER_COMMAND           = config["verifier_command"]
    VERDICT_OUTPUT_PATH        = instance_dir / config["verdict_output_path"]

    sched = config.get("schedule", {})
    # Use literal defaults (not the global names) so that a missing key always
    # resets to a known value rather than inheriting whatever the previous
    # instance left in the global. This prevents cross-instance bleed when the
    # same process loads two instances sequentially.
    SCHEDULE_EXPLORE_EVERY_N         = sched.get("explore_every_N",                5)
    SCHEDULE_EXPLORE_BUDGET          = sched.get("explore_budget",                 2)
    SCHEDULE_MAX_DEBATE_ROUNDS       = sched.get("max_debate_rounds",              2)
    SCHEDULE_SCOUT_EVERY_N           = sched.get("scout_every_N",                 10)
    RECENT_EXPERIMENTS_WINDOW        = sched.get("recent_experiments_window",      5)
    RECENT_EXPERIMENTS_MAX_LINES_PER = sched.get("recent_experiments_max_lines_per", 50)

    # Reset per-run side-channel state on instance reload
    _last_profile_data = {}

    # Set artifact path from instance config
    ARTIFACT_PATH = instance_dir / config["artifact"]

    # Rebuild hard-blocked set from instance.json field + data dir
    hard_blocked_paths: set[Path] = {DATA_DIR}
    for rel in config.get("hard_blocked", []):
        hard_blocked_paths.add(instance_dir / rel)
    HARD_BLOCKED = frozenset(hard_blocked_paths)

    # Rebuild per-agent write permissions now that ARTIFACT_PATH is known
    WRITE_PERMISSIONS["implementation"] = frozenset({ARTIFACT_PATH, PARAMS_JSON, EXPERIMENT_RESULT_MD})
    WRITE_PERMISSIONS["exploration"]    = frozenset({ARTIFACT_PATH, PARAMS_JSON, EXPLORATION_RESULT_MD})
    WRITE_PERMISSIONS["researcher-a"]   = frozenset({PROPOSAL_MD})
    WRITE_PERMISSIONS["researcher-b"]   = frozenset({OBJECTIONS_MD})
    WRITE_PERMISSIONS["attribution"]    = frozenset({UNEXPLORED_IDEAS_MD, DO_NOT_REPEAT_MD})
    WRITE_PERMISSIONS["literature-scout"] = frozenset({REFERENCES_MD})

    # Wire MBMM store paths to instance directory
    mbmm.init(instance_dir)

    # Run linter (warn only — do not fail startup)
    validate_domain_context(instance_dir, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Autoresearch Control Plane")
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to the problem instance directory (must contain instance.json)"
    )
    parser.add_argument("--setup", action="store_true",
                        help="Run baseline benchmark and initialize state files")
    parser.add_argument("--once", action="store_true",
                        help="Run exactly one experiment then exit")
    args = parser.parse_args()

    instance_dir = Path(args.instance_dir).resolve()

    # Load .env from instance directory (instance-specific secrets like API keys)
    _env_file = instance_dir / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

    instance_json = instance_dir / "instance.json"
    if not instance_json.exists():
        print(f"ERROR: No instance.json found in {instance_dir}", file=sys.stderr)
        print("  Create one using the template at autoresearch/template/instance.json", file=sys.stderr)
        sys.exit(1)
    config = load_instance_config(instance_dir)
    apply_instance_config(config, instance_dir)

    if args.setup:
        cmd_setup()
    else:
        cmd_run(run_once=args.once)


if __name__ == "__main__":
    main()
