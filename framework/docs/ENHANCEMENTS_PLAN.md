# AutoResearch Framework — 4 Enhancements Plan

> Reviewed by systems-architect and architecture-reviewer agents.
> All P0/P1 findings incorporated. Design decisions locked below.
> **Implement in order: 2 → 1 → 3 → 4**

---

## Enhancement 2: Structured Failure Analysis Section (implement FIRST)

**Type:** Prompt-only. Zero code delta.
**Why first:** Cheapest, zero risk, highest leverage. Enhancement 3 also depends on
the section headers defined here — they must exist before 3 is implemented.

### Change

In `framework/core/agent_prompts/implementation.md`, **merge** the structured failure
analysis into the existing `## Analysis` section (do NOT add a parallel `## Failure
Analysis` section — that would create two analysis sections and confuse the model and
Enhancement 3's parser).

The `## Analysis` section should include four required sub-fields alongside the
existing dead-forever / revive-condition / cross-compose fields:

```markdown
## Analysis

**Primary cause:** [one sentence: what specifically caused the result — or why it succeeded]
**Evidence:** [what in the output/metrics/verifier confirms this]
**Falsifiable prediction:** [if you ran variant X, you would expect Y]
**Suggested follow-up:** [concrete next experiment]

[existing: dead forever / revive condition / cross-compose reasoning]
```

Apply to PASS and FAIL alike — successful experiments are just as informative.

### Instruction numbering fix

The existing report instructions are numbered 1–6. Add the failure analysis guidance
as part of step 5 (after determining status, before cross-compose reasoning), since
cross-compose reasoning depends on understanding the failure mechanism.

### Coupling contract for Enhancement 3

Enhancement 3 reads `## Analysis` sections from archived experiment results. The exact
heading string is `## Analysis` (case-sensitive, no trailing colon). This is the
canonical section name — do not vary it.

---

## Enhancement 1: PROFILE Line Convention

**Type:** Code + prompt.
**Depends on:** Nothing (can implement standalone).

### Benchmark output contract extension

Instances may optionally emit `PROFILE key=value` lines in benchmark stdout alongside
`METRIC` lines. The framework collects these as auxiliary context but does not use
them for keep/discard decisions.

```
PROFILE cache_misses=4821903
PROFILE peak_memory_kb=102400
PROFILE relaxation_ratio=0.87
```

Rules (mirrors `METRIC` contract):
- `key` is bare identifier, `value` is decimal number.
- If multiple lines share the same key, last one wins.
- If `value` is non-numeric, skip with a warning (do not crash).
- All collected PROFILE lines are available as `{{PROFILE_DATA}}`.
- If no PROFILE lines are emitted, `{{PROFILE_DATA}}` = `"(no profile data collected)"`.

### Code changes in `control_plane.py`

**DO NOT change `run_benchmark()` return type.** It stays `dict[str, float] | None`.
Instead, use a module-level side-channel:

```python
_last_profile_data: dict[str, float] = {}
```

Inside `run_benchmark()`, before returning:
1. Parse all `PROFILE key=value` lines from stdout into a local dict.
2. Apply a cap: keep first **20** PROFILE lines max (guard against token bloat).
3. Write the result to `_last_profile_data` (replace, not append).
4. Return the existing metrics dict unchanged.

On failure / timeout / no output: set `_last_profile_data = {}` before returning `None`.
Reset `_last_profile_data = {}` in `apply_instance_config()` so state doesn't bleed
across instance reloads.

Add `_format_profile_data() -> str` helper:
- If `_last_profile_data` is empty: return `"(no profile data collected)"`.
- Otherwise: return newline-separated `key = value` lines.

Add `PROFILE_DATA` to `_common_subs()` so it's automatically available to all agents:
```python
"PROFILE_DATA": _format_profile_data(),
```

This is preferable to ad-hoc wiring at two call sites — it makes the value available
uniformly without touching individual render paths.

### Prompt changes

In `implementation.md` report phase: add a `### Profile Data` subsection with
`{{PROFILE_DATA}}` to the context block shown to the agent before it writes its report.

In `researcher_a.md`: add a `### Profile Data from last experiment` block with
`{{PROFILE_DATA}}`. Note: Researcher A sees the **previous** experiment's profile (the
debate happens before implementation). This is correct and expected — document it in
the prompt.

### Logging

Log how many PROFILE lines were parsed at DEBUG level after each benchmark run.

---

## Enhancement 3: Rolling Experiment Summaries to Researcher A

**Type:** Code + prompt.
**Depends on:** Enhancement 2 (must be deployed first so `## Analysis` sections exist
in new experiment results).

### New seam constants (configurable via `instance.json`)

```python
RECENT_EXPERIMENTS_WINDOW: int = 5     # number of recent summaries to load; 0 = disabled
RECENT_EXPERIMENTS_MAX_LINES_PER: int = 50  # per-file line cap to control token budget
```

When `RECENT_EXPERIMENTS_WINDOW == 0`: inject `"(no recent experiment summaries)"`.

### New function: `_load_recent_experiment_summaries(n: int) -> str`

Placed near `recent_results()` in `control_plane.py` (they serve related purposes).

Algorithm:
1. Read `total_experiments` from `load_progress()` (which reads `exploitation-progress.json`).
   Do NOT read from master.json — master.json tracks the current best result, not the
   experiment counter. `total_experiments` is the authoritative count and is incremented
   at the end of every `run_one_experiment()` call (control_plane.py:1141).
2. Iterate backwards from `total_experiments` down to 1.
3. For each experiment number, check if `ARTIFACTS_DIR / f"exp-{i:03d}" / "experiment-result.md"` exists.
4. Skip missing directories (ABANDONED, TIMEOUT, implementation failures produce no result file).
4. For files that exist, extract the `## Analysis` section (from the `## Analysis` heading
   to the next `##` heading or EOF). If `## Analysis` is absent (historical pre-Enhancement-2
   experiments), fall back to the full file content.
5. Apply the `RECENT_EXPERIMENTS_MAX_LINES_PER` line cap per file.
6. Collect until `n` valid results are found (or all experiments exhausted).
7. Format as:
   ```
   ### exp-042 (kept, -43.8%)
   [extracted analysis section]

   ### exp-040 (discarded, FAIL-VERIFIER)
   [extracted analysis section]
   ...
   ```
8. If no summaries found (fresh run, all abandoned): return `"(no recent experiment summaries)"`.

**Bootstrapping gap**: Experiments run before Enhancement 2 was deployed will not have
structured `## Analysis` sections. The fallback (full file content) handles this gracefully.

### Wiring in `run_debate()`

Add `RECENT_EXPERIMENTS` to the substitutions dict at both Researcher A call sites
(lines 698–706 and 714–720 in `run_debate()`):

```python
"RECENT_EXPERIMENTS": _load_recent_experiment_summaries(RECENT_EXPERIMENTS_WINDOW),
```

### Prompt change

In `researcher_a.md`, add a section:
```markdown
## Recent Experiment Summaries
{{RECENT_EXPERIMENTS}}
```

### Logging

Log how many summaries were loaded and their total character count at DEBUG level.

### Tests

- Empty artifacts directory → returns `"(no recent experiment summaries)"`
- Gaps in numbering (abandoned experiments) → skips gracefully, returns best-effort list
- Experiments missing `## Analysis` → falls back to full file content
- `RECENT_EXPERIMENTS_MAX_LINES_PER` truncation → last line cut correctly
- `RECENT_EXPERIMENTS_WINDOW = 0` → returns disable message immediately

---

## Enhancement 4: Failure Hypothesis Fields in Idea Bank — Hardened v2 (implement LAST)

**Type:** Code + prompt (mbmm.py + attribution.md + control_plane.py).
**Depends on:** Enhancements 2 and 3.

### Semantic decision: field ownership

| Field | Who sets it | When | Meaning |
|-------|------------|------|---------|
| `blocked_by` | Researcher agent | Before running | "Don't attempt this until X is true" (pre-execution block) |
| `failure_hypothesis` | Attribution agent | After FAIL-METRIC only | Tentative cause, evidence-grounded, one sentence |
| `failure_confidence` | Attribution agent | With `failure_hypothesis` | `"low"` / `"medium"` / `"high"` |
| `failure_hypothesis_from` | Attribution agent | With `failure_hypothesis` | Provenance: `"exp-042"` |
| `revive_condition` | Attribution agent | After FAIL-METRIC only | Concrete retry condition, one sentence |

`blocked_by` is researcher-set before execution. The four failure-memory fields are
attribution-set after a FAIL-METRIC only. These are structurally and temporally distinct.

### Design principle: high-confidence memory only

The main risk is the attribution agent writing plausible-sounding blame that future
researchers treat as truth. The entire schema is designed around this:

- **Default is silence.** If the cause is ambiguous, omit all four fields.
- **No speculative storage.** Only concrete, evidence-grounded hypotheses get written.
- **Confidence-ranked overwrite.** A weaker attribution cannot overwrite a stronger one.
- **Provenance required.** Every stored hypothesis carries its source experiment ID.
- **Scoped blame.** Penalize signals target specific idea IDs only. Blame does not
  propagate to mechanism families unless the agent explicitly penalizes multiple IDs.

### New fields in MBMM idea schema

All four fields are optional, default `None`. Use `.get()` everywhere for backward
compat with existing idea-store.json files.

```python
idea = {
    ...existing fields...,
    "failure_hypothesis":      None,  # str | None — one sentence, evidence-grounded
    "failure_confidence":      None,  # "low" | "medium" | "high" | None
    "failure_hypothesis_from": None,  # "exp-042" | None — provenance
    "revive_condition":        None,  # str | None — one sentence, concrete retry condition
}
```

### Changes to `mbmm.py`

**Confidence ranking constant:**
```python
_CONF_RANK: dict[str | None, int] = {"low": 1, "medium": 2, "high": 3, None: 0}

def _should_overwrite_failure(old_conf: str | None, new_conf: str | None) -> bool:
    """Only write if new confidence strictly exceeds existing confidence."""
    if new_conf is None:
        return False
    return _CONF_RANK.get(new_conf, 0) > _CONF_RANK.get(old_conf, 0)
```

**`apply_signals()` — penalize branch** (lines 292–296): after adjusting `base_score`:
```python
new_conf = signal.get("failure_confidence")
if "failure_hypothesis" in signal and _should_overwrite_failure(
    idea.get("failure_confidence"), new_conf
):
    idea["failure_hypothesis"]      = signal["failure_hypothesis"]
    idea["failure_confidence"]      = new_conf
    idea["failure_hypothesis_from"] = signal.get("failure_hypothesis_from")
    idea["revive_condition"]        = signal.get("revive_condition")
```

Note: all four fields are written atomically. A partial update (e.g. `failure_hypothesis`
without `failure_confidence`) is never written — the overwrite check gates all four.

**`apply_signals()` — revive branch** (lines 301–306): clear all four fields:
```python
idea["failure_hypothesis"]      = None
idea["failure_confidence"]      = None
idea["failure_hypothesis_from"] = None
idea["revive_condition"]        = None
```

**On kept citation outcome** (`record_citation_outcome`, kept=True): also clear all four
failure-memory fields for the cited idea. A later success explicitly invalidates prior blame.

**`format_window_for_prompt()`**: if `failure_hypothesis` is set and non-empty, render
as tentative memory with provenance and confidence (not as a hard blocker):
```
  failure_memory: [medium, from exp-042] extra sorting likely dominated runtime
  revive_condition: worth retrying only if sorting is hoisted out of hot loop
```

Render `failure_memory` only if `failure_confidence` is `"medium"` or `"high"`.
Low-confidence hypotheses are silently suppressed from the idea window (they remain
in the store for audit but do not influence Researcher A's ideation).

### Changes to `attribution.md`

Update the signal schema example:
```json
{
  "id": "idea-042",
  "signal": "penalize",
  "reason": "one sentence causal connection to metric regression",
  "failure_hypothesis": "Per-iteration sorting in the hot loop added O(k log k) overhead per edge relaxation, directly explaining the 18% regression.",
  "failure_confidence": "medium",
  "failure_hypothesis_from": "exp-042",
  "revive_condition": "Worth retrying only if sorting is hoisted out of the relaxation loop into a one-time preprocessing step."
}
```

**Strict prompt rules to add in attribution.md:**

1. **FAIL-VERIFIER → no penalize signal, no failure memory.** A correctness failure is
   an implementation bug, not evidence against the idea. Write a detailed `## Analysis`
   section (surfaced to Researcher A via Enhancement 3), but do not touch idea scores
   or memory. Use no signal, or `neutral` if the format requires one.

2. **FAIL-METRIC, ambiguous cause → omit all failure fields.** If the result does not
   contain concrete evidence isolating the cause, do not populate `failure_hypothesis`.
   Silence is better than a guess.

3. **One sentence only** for `failure_hypothesis` and `revive_condition`. No essays.
   The sentence must reference concrete evidence from the experiment result (observed
   behavior, metric values, verifier output).

4. **`failure_hypothesis` must start with observation, end with inference.** Pattern:
   `"[observed X in the result], [therefore Y is the likely cause]."` This forces the
   agent to ground the inference in evidence rather than speculate from first principles.

5. **Confidence levels:**
   - `"high"`: result contains direct evidence strongly isolating the cause
   - `"medium"`: cause is plausible and grounded but not fully isolated
   - `"low"`: avoid; if the hypothesis isn't medium-confidence, omit it instead

6. **If `failure_hypothesis` is absent, `failure_confidence` and `revive_condition`
   must also be absent.** Never store partial hypothesis records.

The invariant rule (encoded in both code and prompt):
**Failure memory is only for FAIL-METRIC.** FAIL-VERIFIER generates analysis for
Enhancement 3 but never blame for idea memory.

### Logging

Log at DEBUG: when failure memory is written (confidence + source exp), when it is
suppressed by the confidence-rank check, and when it is cleared (revive or kept-success).

### Tests

1. FAIL-VERIFIER signal → no failure memory written (all four fields stay None).
2. FAIL-METRIC, signal has no `failure_hypothesis` → memory unchanged.
3. FAIL-METRIC, `failure_confidence = "medium"` into empty slot → written.
4. `failure_confidence = "medium"` cannot overwrite existing `"high"`.
5. `failure_confidence = "high"` can overwrite existing `"medium"`.
6. Revive signal clears all four fields.
7. Kept citation outcome for the same idea clears all four fields.
8. `format_window_for_prompt()` suppresses `"low"` confidence from rendered window.
9. `format_window_for_prompt()` renders `"medium"` / `"high"` with `[conf, from exp-N]` prefix.

---

## Cross-Cutting Notes

### Atomic deployment per enhancement
Prompt and code changes for each enhancement must be deployed together. An unmatched
`{{PROFILE_DATA}}` placeholder is benign (rendered as literal text by `render_template()`
per its no-match passthrough), but noisy. Commit each enhancement as a single commit.

### Token budget
Enhancements 1, 3, and 4 all add content to `researcher_a.md`. Estimates:
- Enhancement 1: ~5–10 lines (20 PROFILE lines max, formatted)
- Enhancement 3: ~250 lines (5 × 50 lines)
- Enhancement 4: ~2 lines per idea × 10 ideas = ~20 lines

Total additional context per debate round: ~280 lines (~3500 tokens at ~12 chars/token).
`researcher_a.md` already injects ~7 context blocks. Monitor for attention degradation
if outputs become less focused. Emergency lever: set `RECENT_EXPERIMENTS_WINDOW=0`.

### Rollback
`RECENT_EXPERIMENTS_WINDOW=0` disables Enhancement 3 without code changes.
PROFILE cap and `_format_profile_data()` ensure Enhancement 1 has bounded impact.
Enhancement 4 is additive to the idea schema; old store files load correctly via `.get()`.
