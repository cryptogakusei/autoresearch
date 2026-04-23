#!/usr/bin/env python3
"""
Multi-Bandit Memory Management (MBMM)
======================================
Manages the idea store for the SSSP autoresearch loop.
Replaces the flat unexplored-ideas.md with a scored JSON store.

Selection strategy (per researcher call):
  N_EXPLOIT top-scored ideas   — exploitation
  N_EXPLORE_RECENT random      — recently added, not yet top-ranked
  N_EXPLORE_OLD    random      — older buried ideas (prevents permanent burial)

Scoring:
  Phase 1 (< UCB_MIN_OBSERVATIONS citations):
    runtime_score = base_score × age_decay × window_penalty   [heuristic]

  Phase 2 (>= UCB_MIN_OBSERVATIONS citations):
    Blends heuristic → UCB1 as citation count grows:
    UCB1 = (times_kept / times_cited) + sqrt(2 * ln(total_experiments) / times_cited)

  base_score    — mutable by recorder/citation signals (bounded ±0.2)
  age_decay     = 1 / log2(experiments_since_added + 2)
  window_penalty = max(0.5, 0.90 ^ times_in_window)
                  penalises ideas shown but never cited

References:
  - Generative Agents (Park et al., UIST 2023) — scored memory retrieval
  - POLCA (Ren et al., 2026) — priority queue + epsilon-greedy for LLM optimisation
  - Lost in the Middle (Liu et al., TACL 2023) — windowed context over flat files
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Default: resolve relative to this file (backwards-compatible for existing
# sessions that have not called init() yet).
_BASE = Path(__file__).parent.resolve()
IDEA_STORE_PATH   = _BASE / "idea-store.json"
IDEA_ARCHIVE_PATH = _BASE / "idea-store-archive.json"


def init(instance_dir: Path) -> None:
    """Set store paths to the instance directory.

    Must be called by control_plane.py immediately after loading instance
    config, before any load_store() or save_store() calls.
    """
    global IDEA_STORE_PATH, IDEA_ARCHIVE_PATH
    instance_dir = Path(instance_dir).resolve()
    IDEA_STORE_PATH   = instance_dir / "idea-store.json"
    IDEA_ARCHIVE_PATH = instance_dir / "idea-store-archive.json"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
N_EXPLOIT        = 6    # top-N by runtime_score
N_EXPLORE_RECENT = 2    # random from ideas added in last RECENT_THRESHOLD exps
N_EXPLORE_OLD    = 2    # random from ideas added before RECENT_THRESHOLD exps
RECENT_THRESHOLD = 20   # experiments — boundary between "recent" and "old"

GC_SCORE_THRESHOLD   = 0.05   # archive if runtime_score < this
GC_MIN_AGE           = 15     # only GC ideas older than this many experiments

MAX_BOOST    = 0.20   # maximum base_score increase per recorder signal
MAX_PENALIZE = 0.15   # maximum base_score decrease per recorder signal
BASE_SCORE_MIN = 0.10
BASE_SCORE_MAX = 1.00

INITIAL_SCORE_SCOUT    = 0.80   # literature-backed ideas start higher
INITIAL_SCORE_RECORDER = 0.50   # experiment-inferred ideas

# UCB blending
UCB_MIN_OBSERVATIONS = 3    # need at least this many citations before UCB kicks in
UCB_FULL_WEIGHT_AT   = 10   # at this many citations, UCB has full weight (heuristic weight = 0)


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def load_store() -> list[dict[str, Any]]:
    try:
        return json.loads(IDEA_STORE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_store(ideas: list[dict[str, Any]]) -> None:
    IDEA_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDEA_STORE_PATH.write_text(json.dumps(ideas, indent=2))


def load_archive() -> list[dict[str, Any]]:
    try:
        return json.loads(IDEA_ARCHIVE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_archive(ideas: list[dict[str, Any]]) -> None:
    IDEA_ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDEA_ARCHIVE_PATH.write_text(json.dumps(ideas, indent=2))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_runtime_score(
    idea: dict[str, Any],
    current_exp: int,
    total_experiments: int = 0,
) -> float:
    """
    Scoring blends two signals:

    1. Heuristic (always computed):
       base_score × age_decay × window_penalty

    2. UCB (when times_cited >= UCB_MIN_OBSERVATIONS):
       empirical_success_rate + sqrt(2 * ln(total_experiments) / times_cited)

    The two are blended: heuristic → UCB as times_cited grows from
    UCB_MIN_OBSERVATIONS to UCB_FULL_WEIGHT_AT.
    """
    base_score      = float(idea.get("base_score", 0.5))
    added_at        = int(idea.get("added_at_exp", 0))
    times_in_window = int(idea.get("times_in_window", 0))

    age             = max(0, current_exp - added_at)
    age_decay       = 1.0 / math.log2(age + 2)
    effective_shown = min(times_in_window, 10)
    window_penalty  = max(0.5, 0.90 ** effective_shown)
    heuristic       = base_score * age_decay * window_penalty

    times_cited = int(idea.get("times_cited", 0))
    times_kept  = int(idea.get("times_kept", 0))

    if times_cited < UCB_MIN_OBSERVATIONS or total_experiments < 2:
        return heuristic

    empirical_rate    = times_kept / times_cited
    exploration_bonus = math.sqrt(2.0 * math.log(total_experiments) / times_cited)
    ucb               = empirical_rate + exploration_bonus

    # Smooth weight: 0 at UCB_MIN_OBSERVATIONS citations, 1.0 at UCB_FULL_WEIGHT_AT
    ucb_weight = min(
        1.0,
        (times_cited - UCB_MIN_OBSERVATIONS)
        / max(1, UCB_FULL_WEIGHT_AT - UCB_MIN_OBSERVATIONS),
    )
    return (1.0 - ucb_weight) * heuristic + ucb_weight * ucb


# ---------------------------------------------------------------------------
# Window selection
# ---------------------------------------------------------------------------

def load_idea_window(
    current_exp: int,
    total_experiments: int = 0,
) -> tuple[list[dict], list[str]]:
    """
    Returns (selected_ideas, selected_ids).

    selected_ideas — the ideas to render in the researcher prompt
    selected_ids   — their IDs, for tracking via mark_ideas_shown()
    """
    ideas  = load_store()
    active = [i for i in ideas if i.get("status", "active") == "active"]

    if not active:
        return [], []

    # Score and sort
    scored = sorted(
        ((compute_runtime_score(i, current_exp, total_experiments), i) for i in active),
        key=lambda x: x[0],
        reverse=True,
    )

    exploit_ideas = [i for _, i in scored[:N_EXPLOIT]]
    remaining     = [i for _, i in scored[N_EXPLOIT:]]

    # Stratified explore: recent vs old
    recent = [i for i in remaining
              if current_exp - int(i.get("added_at_exp", 0)) <= RECENT_THRESHOLD]
    old    = [i for i in remaining
              if current_exp - int(i.get("added_at_exp", 0)) > RECENT_THRESHOLD]

    explore_recent = random.sample(recent, min(N_EXPLORE_RECENT, len(recent))) if recent else []
    explore_old    = random.sample(old,    min(N_EXPLORE_OLD,    len(old)))    if old    else []

    # Fill shortfalls across strata
    shortfall_r = N_EXPLORE_RECENT - len(explore_recent)
    shortfall_o = N_EXPLORE_OLD    - len(explore_old)
    if shortfall_r > 0:
        extras = [i for i in old if i not in explore_old]
        explore_old += random.sample(extras, min(shortfall_r, len(extras)))
    if shortfall_o > 0:
        extras = [i for i in recent if i not in explore_recent]
        explore_recent += random.sample(extras, min(shortfall_o, len(extras)))

    selected = exploit_ideas + explore_recent + explore_old
    return selected, [i["id"] for i in selected]


def mark_ideas_shown(idea_ids: list[str], current_exp: int) -> None:
    """Increment times_in_window and update last_in_window_exp for shown ideas."""
    if not idea_ids:
        return
    ideas  = load_store()
    id_set = set(idea_ids)
    for idea in ideas:
        if idea["id"] in id_set:
            idea["times_in_window"]    = idea.get("times_in_window", 0) + 1
            idea["last_in_window_exp"] = current_exp
    save_store(ideas)


def format_window_for_prompt(
    selected: list[dict[str, Any]],
    current_exp: int,
    total_experiments: int = 0,
) -> str:
    """Render the idea window as compact markdown for the researcher prompt."""
    if not selected:
        return "(idea store is empty — propose a novel direction)"

    lines = [
        f"**{len(selected)} candidate ideas** "
        f"(top-{N_EXPLOIT} scored + {N_EXPLORE_RECENT + N_EXPLORE_OLD} explored):\n"
    ]
    for idea in selected:
        score = compute_runtime_score(idea, current_exp, total_experiments)
        lines.append(f"### [{idea['id']}] {idea['title']}")
        lines.append(f"mechanism: {idea['mechanism']}")
        lines.append(f"signal: {idea['signal']}")
        lines.append(
            f"score: {score:.2f} | source: {idea.get('source','?')} "
            f"| added: exp-{idea.get('added_at_exp', '?')}"
        )
        if idea.get("blocked_by"):
            lines.append(f"blocked_by: {idea['blocked_by']}")
        # Enhancement 4: render failure memory as tentative (medium/high confidence only)
        fh   = idea.get("failure_hypothesis")
        conf = idea.get("failure_confidence")
        if fh and conf in ("medium", "high"):
            from_exp = idea.get("failure_hypothesis_from", "unknown")
            lines.append(f"failure_memory: [{conf}, from {from_exp}] {fh}")
            rc = idea.get("revive_condition")
            if rc:
                lines.append(f"revive_condition: {rc}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Enhancement 4: failure-memory helpers
# ---------------------------------------------------------------------------

# Confidence ranking for the overwrite rule: a stored hypothesis can only be
# replaced by one with strictly higher confidence.
_CONF_RANK: dict[str | None, int] = {"low": 1, "medium": 2, "high": 3, None: 0}


def _should_overwrite_failure(old_conf: str | None, new_conf: str | None) -> bool:
    """Return True only if new_conf strictly exceeds old_conf.

    This prevents a weaker attribution from overwriting a stronger one,
    and prevents writing when no confidence is provided.
    """
    if new_conf is None:
        return False
    return _CONF_RANK.get(new_conf, 0) > _CONF_RANK.get(old_conf, 0)


def _clear_failure_memory(idea: dict[str, Any]) -> None:
    """Clear all four failure-memory fields atomically."""
    idea["failure_hypothesis"]      = None
    idea["failure_confidence"]      = None
    idea["failure_hypothesis_from"] = None
    idea["revive_condition"]        = None


# ---------------------------------------------------------------------------
# Signal application (called by control plane after recorder/scout output)
# ---------------------------------------------------------------------------

def apply_signals(
    signals:     list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
    current_exp: int,
) -> dict[str, int]:
    """
    Apply recorder/scout update payloads to the store.

    signals:     list of {id, signal: "boost"|"penalize"|"supersede"|"revive", reason}
    new_entries: list of compact idea dicts (id and added_at_exp assigned here)

    Returns {"signals_applied": N, "ideas_added": M}
    """
    ideas    = load_store()
    idea_map = {i["id"]: i for i in ideas}

    signals_applied = 0
    for sig in signals:
        idea_id     = sig.get("id", "")
        signal_type = sig.get("signal", "")
        if idea_id not in idea_map:
            continue
        idea = idea_map[idea_id]

        if signal_type == "boost":
            idea["base_score"] = min(
                BASE_SCORE_MAX,
                float(idea.get("base_score", 0.5)) + MAX_BOOST,
            )
            signals_applied += 1
        elif signal_type == "penalize":
            idea["base_score"] = max(
                BASE_SCORE_MIN,
                float(idea.get("base_score", 0.5)) - MAX_PENALIZE,
            )
            signals_applied += 1
            # Enhancement 4: write failure-memory fields only if confidence is strictly
            # higher than the existing stored confidence (prevents weak attributions
            # from overwriting stronger ones).
            new_conf = sig.get("failure_confidence")
            if "failure_hypothesis" in sig and _should_overwrite_failure(
                idea.get("failure_confidence"), new_conf
            ):
                idea["failure_hypothesis"]      = sig["failure_hypothesis"]
                idea["failure_confidence"]      = new_conf
                idea["failure_hypothesis_from"] = sig.get("failure_hypothesis_from")
                idea["revive_condition"]        = sig.get("revive_condition")
                log.debug(
                    "failure_memory written: id=%s conf=%s from=%s",
                    idea_id, new_conf, sig.get("failure_hypothesis_from"),
                )
            elif "failure_hypothesis" in sig:
                log.debug(
                    "failure_memory suppressed (existing conf=%s >= new conf=%s): id=%s",
                    idea.get("failure_confidence"), new_conf, idea_id,
                )
        elif signal_type == "supersede":
            idea["status"] = f"superseded_by:exp-{current_exp}"
            signals_applied += 1
        elif signal_type == "revive":
            idea["base_score"]       = float(idea.get("initial_score", 0.5))
            idea["times_in_window"]  = 0
            idea["last_in_window_exp"] = 0
            idea["status"]           = "active"
            _clear_failure_memory(idea)
            log.debug("failure_memory cleared on revive: id=%s", idea_id)
            signals_applied += 1

    # Assign IDs and append new entries
    existing_ids = {i["id"] for i in ideas}
    existing_nums = [
        int(i["id"].split("-")[1])
        for i in ideas
        if re.match(r"idea-\d+", i["id"])
    ]
    next_n = max(existing_nums, default=0) + 1

    ideas_added = 0
    for entry in new_entries:
        new_id = f"idea-{next_n:03d}"
        while new_id in existing_ids:
            next_n += 1
            new_id = f"idea-{next_n:03d}"

        initial_score = float(entry.get("initial_score", INITIAL_SCORE_RECORDER))
        ideas.append({
            "id":                new_id,
            "title":             entry.get("title", ""),
            "mechanism":         entry.get("mechanism", ""),
            "signal":            entry.get("signal", ""),
            "status":            "active",
            "source":            entry.get("source", "recorder"),
            "initial_score":     initial_score,
            "base_score":        initial_score,
            "added_at_exp":      current_exp,
            "times_in_window":   0,
            "last_in_window_exp": 0,
            "times_cited":       0,
            "times_kept":        0,
            "blocked_by":        entry.get("blocked_by", None),
            "superseded_by":     None,
        })
        existing_ids.add(new_id)
        next_n  += 1
        ideas_added += 1

    save_store(ideas)
    return {"signals_applied": signals_applied, "ideas_added": ideas_added}


def record_citation_outcome(
    cited_ids:         list[str],
    kept:              bool,
    fail_type:         str,
    current_exp:       int,
) -> dict[str, int]:
    """
    Record the outcome of an experiment for ideas explicitly cited by the researcher.

    cited_ids:  idea IDs the researcher attributed their proposal to
    kept:       True if the experiment was kept (improved runtime)
    fail_type:  "FAIL-METRIC" | "FAIL-VERIFIER" | "" (for kept experiments)

    Rules:
      kept          → increment times_cited + times_kept, boost base_score
      FAIL-METRIC   → increment times_cited, penalize base_score
      FAIL-VERIFIER → no signal (correctness bug, not the idea's fault)

    Returns {"outcomes_recorded": N}
    """
    if not cited_ids:
        return {"outcomes_recorded": 0}

    ideas    = load_store()
    idea_map = {i["id"]: i for i in ideas}

    outcomes_recorded = 0
    for idea_id in cited_ids:
        if idea_id not in idea_map:
            continue
        idea = idea_map[idea_id]

        if fail_type == "FAIL-VERIFIER":
            # Implementation bug — do not penalize the idea
            continue

        idea["times_cited"] = idea.get("times_cited", 0) + 1

        if kept:
            idea["times_kept"] = idea.get("times_kept", 0) + 1
            idea["base_score"] = min(
                BASE_SCORE_MAX,
                float(idea.get("base_score", 0.5)) + MAX_BOOST,
            )
            # Enhancement 4: a later success is the strongest refutation of prior blame.
            # Clear stale failure memory so old hypotheses don't persist after validation.
            if idea.get("failure_hypothesis") is not None:
                _clear_failure_memory(idea)
                log.debug("failure_memory cleared on kept citation: id=%s", idea_id)
        else:  # FAIL-METRIC
            idea["base_score"] = max(
                BASE_SCORE_MIN,
                float(idea.get("base_score", 0.5)) - MAX_PENALIZE,
            )

        outcomes_recorded += 1

    save_store(ideas)
    return {"outcomes_recorded": outcomes_recorded}


# ---------------------------------------------------------------------------
# Garbage collection
# ---------------------------------------------------------------------------

def run_gc(current_exp: int, total_experiments: int = 0) -> int:
    """
    Archive ideas whose runtime_score has dropped below GC_SCORE_THRESHOLD
    and are old enough (age >= GC_MIN_AGE).  Also archives superseded ideas.
    Returns count of ideas archived.

    total_experiments must match the value used by load_idea_window() so that
    GC and selection use the same scoring regime (heuristic vs UCB blend).
    """
    ideas   = load_store()
    archive = load_archive()

    to_keep:    list[dict] = []
    to_archive: list[dict] = []

    for idea in ideas:
        status = idea.get("status", "active")
        if status != "active":
            to_archive.append(idea)
            continue
        age   = current_exp - int(idea.get("added_at_exp", 0))
        score = compute_runtime_score(idea, current_exp, total_experiments)
        if score < GC_SCORE_THRESHOLD and age >= GC_MIN_AGE:
            idea["status"]         = "archived"
            idea["archived_at_exp"] = current_exp
            to_archive.append(idea)
        else:
            to_keep.append(idea)

    if to_archive:
        archive.extend(to_archive)
        save_archive(archive)
        save_store(to_keep)

    return len(to_archive)


# ---------------------------------------------------------------------------
# Migration from unexplored-ideas.md
# ---------------------------------------------------------------------------

def migrate_from_flat_file(md_path: Path) -> int:
    """
    Parse unexplored-ideas.md (the old append-only format) and write
    idea-store.json.  Uses deterministic regex — no LLM call.
    Returns count of successfully parsed entries; raises ValueError on zero.
    """
    text = md_path.read_text()

    # Split on ## section headers
    raw_blocks = re.split(r'\n(?=## )', text)

    ideas:            list[dict] = []
    skipped_blocks:   list[str]  = []
    next_n: int                  = 1

    # Count all well-formed idea blocks upfront for count validation
    candidate_blocks = [
        b.strip() for b in raw_blocks
        if b.strip()
        and b.strip().startswith("## ")
        and not b.strip().startswith("# ")
        and b.strip() != "(none yet)"
    ]
    expected_count = len(candidate_blocks)

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        # Skip file header and placeholder lines
        if block.startswith("# ") or block == "(none yet)":
            continue
        if not block.startswith("## "):
            continue

        first_line = block.splitlines()[0]
        title_m = re.match(r"##\s*\[.*?\]\s*(.*)", first_line)
        if not title_m:
            skipped_blocks.append(first_line[:80])
            continue
        title = title_m.group(1).strip()

        # Source experiment → added_at_exp approximation
        src_m    = re.search(r"(?:Exploration ID|Experiment ID):\s*(?:exp|explore)-(\d+)", block)
        added_at = int(src_m.group(1)) if src_m else 0

        # Thesis → mechanism (first sentence)
        thesis_m  = re.search(
            r"Thesis:\s*(.+?)(?=\n(?:Why it might work|Runs attempted|Why parked|Revive|Cross|Next|Added))",
            block, re.DOTALL,
        )
        mechanism = thesis_m.group(1).strip().replace("\n", " ") if thesis_m else title
        mechanism = mechanism[:250]

        # Next step → signal
        next_m = re.search(r"Next step:\s*(.+?)(?=\nAdded:|\Z)", block, re.DOTALL)
        signal = next_m.group(1).strip().replace("\n", " ") if next_m else "see revive condition"
        signal = signal[:250]

        # Revive condition → blocked_by
        revive_m  = re.search(
            r"Revive condition:\s*(.+?)(?=\n(?:Cross|Next|Added)|\Z)",
            block, re.DOTALL,
        )
        blocked_by = revive_m.group(1).strip().replace("\n", " ")[:200] if revive_m else None

        ideas.append({
            "id":                f"idea-{next_n:03d}",
            "title":             title,
            "mechanism":         mechanism,
            "signal":            signal,
            "status":            "active",
            "source":            "recorder",
            "initial_score":     INITIAL_SCORE_RECORDER,
            "base_score":        INITIAL_SCORE_RECORDER,
            "added_at_exp":      added_at,
            "times_in_window":   0,
            "last_in_window_exp": 0,
            "blocked_by":        blocked_by,
            "superseded_by":     None,
        })
        next_n += 1

    if not ideas:
        raise ValueError(
            f"Migration parsed 0 ideas from {md_path} — check input format"
        )

    if len(ideas) != expected_count:
        skipped_summary = "\n  ".join(skipped_blocks) if skipped_blocks else "(no title_m mismatches — check block splitting)"
        raise ValueError(
            f"Migration count mismatch: expected {expected_count} idea blocks, "
            f"parsed {len(ideas)}. Skipped blocks:\n  {skipped_summary}"
        )

    save_store(ideas)
    return len(ideas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "migrate":
        md = _BASE / "unexplored-ideas.md"
        count = migrate_from_flat_file(md)
        print(f"Migrated {count} ideas → {IDEA_STORE_PATH}")

    elif cmd == "status":
        exp_n  = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        ideas  = load_store()
        active = [i for i in ideas if i.get("status") == "active"]
        print(f"Store: {len(ideas)} total, {len(active)} active")
        selected, ids = load_idea_window(exp_n)
        print(f"Window (current_exp={exp_n}): {ids}")
        print(format_window_for_prompt(selected, exp_n))

    elif cmd == "gc":
        exp_n    = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        archived = run_gc(exp_n, total_experiments=exp_n)
        print(f"GC: archived {archived} ideas")

    else:
        print("Usage: python3 mbmm.py migrate | status [exp_n] | gc [exp_n]")
