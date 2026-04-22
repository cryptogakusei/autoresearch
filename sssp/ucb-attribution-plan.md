# UCB Attribution System
## Implementation Summary

---

### Problem

The MBMM scoring system had no connection to actual experiment outcomes. Ideas were
scored by a heuristic (`base_score × age_decay × window_penalty`) and updated by
the recorder agent's inferred signals — but there was no direct attribution between
"idea X was shown" and "experiment Y was kept or discarded."

This meant:
- A good idea that led to a kept experiment got no direct reward
- A bad idea that led to a failed experiment got no direct penalty
- The scoring model could not learn from outcomes over time

---

### Solution

Three-layer fix:

1. **Attribution** — researcher explicitly cites which ideas informed the proposal
2. **Outcome recording** — control plane maps experiment result back to cited ideas
3. **UCB scoring** — scoring formula transitions from heuristic to UCB1 as citation data accumulates

---

### Changes

#### `agent_prompts/researcher_a.md`
Added a required `Ideas cited:` field to the proposal output format:

```
Ideas cited: <comma-separated IDs from the candidate window, e.g. "idea-042, idea-031" — or NONE>
```

The researcher must explicitly attribute which ideas from the window informed the
proposal. This solves the credit assignment problem: instead of guessing which of
the 10 window ideas drove the proposal, the researcher tells us directly.

---

#### `mbmm.py`

**New schema fields** (added to every new idea entry):
```json
"times_cited": 0,
"times_kept":  0
```

**New function: `record_citation_outcome(cited_ids, kept, fail_type, current_exp)`**

Called by the control plane after each experiment outcome is determined.

| Outcome | Effect on cited ideas |
|---|---|
| `kept` | `times_cited += 1`, `times_kept += 1`, `base_score += 0.20` (capped at 1.0) |
| `FAIL-METRIC` | `times_cited += 1`, `base_score -= 0.15` (floored at 0.1) |
| `FAIL-VERIFIER` | No signal — correctness bug is not the idea's fault |

**Updated scoring: `compute_runtime_score(idea, current_exp, total_experiments=0)`**

The formula now blends two signals depending on how much citation data exists:

```
Phase 1 — fewer than UCB_MIN_OBSERVATIONS (3) citations:
  score = base_score × age_decay × window_penalty        [pure heuristic]

Phase 2 — 3+ citations:
  heuristic = base_score × age_decay × window_penalty
  UCB1      = (times_kept / times_cited) + sqrt(2 × ln(total_experiments) / times_cited)
  ucb_weight = min(1.0, (times_cited - 3) / (10 - 3))   [0 at 3 citations, 1.0 at 10]
  score = (1 - ucb_weight) × heuristic + ucb_weight × UCB1
```

The blend means:
- Below 3 citations: pure heuristic (not enough data for UCB)
- 3–10 citations: smooth transition from heuristic to UCB
- 10+ citations: pure UCB1

UCB1 naturally balances exploitation (high `times_kept / times_cited`) and
exploration (ideas with few citations get a large `sqrt(...)` bonus).

**Constants:**
```python
UCB_MIN_OBSERVATIONS = 3    # citations needed before UCB activates
UCB_FULL_WEIGHT_AT   = 10   # citations at which UCB has full weight
```

---

#### `control_plane.py`

**New helper: `_parse_cited_ids(proposal_content)`**
Extracts idea IDs from the `Ideas cited:` line of a proposal using regex.
Returns `[]` if the line says `NONE` or is absent.

**`run_debate` return signature changed:**
```python
# Before
(final_proposal, confidence) = run_debate(master, progress)

# After
(final_proposal, confidence, cited_ids) = run_debate(master, progress)
```

**`record_citation_outcome` called after outcome is determined:**
```python
# kept branch
mbmm.record_citation_outcome(cited_ids, kept=True,  fail_type="",       current_exp=exp_n)

# discarded branch
mbmm.record_citation_outcome(cited_ids, kept=False, fail_type=reason,   current_exp=exp_n)
# where reason = "FAIL-METRIC" | "FAIL-VERIFIER"
```

**`load_idea_window` and `format_window_for_prompt` now receive `total_experiments`**
so the UCB formula has access to the global experiment count for the exploration bonus.

---

### Activation timeline

The UCB component is **inert at deployment** — all existing ideas have
`times_cited = 0`, so the heuristic runs unchanged. UCB activates
automatically, idea by idea, as attribution data accumulates:

```
Experiments 1–~30:   pure heuristic for all ideas
Experiments ~30–~80: mixed — well-cited ideas use UCB, uncited use heuristic
Experiments ~80+:    most active ideas on UCB; new ideas start on heuristic
```

No configuration change is needed for the transition.

---

### Known limitations / future work

- **Existing ideas have no citation history** — the 70 ideas in the current store
  all start at `times_cited = 0`. They will accumulate data from this point forward.
- **Refinement rounds** — if researcher A refines a proposal across multiple debate
  rounds, only the final proposal's `Ideas cited:` field is used. Ideas cited in
  earlier rounds but dropped in refinement do not get attributed.
- **Multi-idea proposals** — if the researcher cites 3 ideas, all 3 get equal
  credit/penalty. There is no weighting by how much each idea contributed.
- **UCB1 is an upper confidence bound, not a posterior** — Thompson Sampling
  (Beta distribution per idea) would be more principled but requires more
  implementation. UCB1 is a reasonable first step.
