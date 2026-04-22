# Multi-Bandit Memory Management (MBMM)
## Design Plan v2 (reviewed + revised)

---

### Problem
`unexplored-ideas.md` was a flat 637-line append-only file passed verbatim to every
researcher prompt. Three failure modes:

1. **Lost-in-the-middle bias** (Liu et al., TACL 2023) — LLMs attend strongly to
   the beginning and end of context; middle content is largely ignored.
2. **Token waste** — full 637-line file on every researcher API call.
3. **No signal decay** — an idea from exp-001 competed equally with a fresh idea
   from exp-075 regardless of relevance.

---

### Literature basis

| Paper | Contribution |
|---|---|
| Lost in the Middle (Liu et al., TACL 2023) | U-shaped attention; flat lists are architecturally broken |
| Generative Agents (Park et al., UIST 2023) | Scored retrieval: recency × importance × relevance |
| POLCA (Ren et al., 2026) | Priority queue + epsilon-greedy for LLM optimisation loops |
| MemGPT (Packer et al., 2023) | Page, don't dump — windowed context over large stores |
| AIDE (Jiang et al., 2025) | Search tree framing; structural exploration over flat queues |

---

### Architecture

#### Idea Store: `idea-store.json`
Replaces `unexplored-ideas.md`. Each entry:

```json
{
  "id":                "idea-042",
  "title":             "Dial's bucket queue with lazy deletion",
  "mechanism":         "replace 4-ary heap with circular bucket array, skip stale pops",
  "signal":            "+10-20% if Δ tuned to edge weight distribution",
  "status":            "active",
  "source":            "recorder | scout",
  "initial_score":     0.5,
  "base_score":        0.62,
  "added_at_exp":      8,
  "times_in_window":   3,
  "last_in_window_exp": 45,
  "blocked_by":        "revive condition or null",
  "superseded_by":     null
}
```

#### Scoring (control plane only — no LLM)

```
runtime_score = base_score × age_decay × window_penalty

age_decay      = 1 / log2(experiments_since_added + 2)
window_penalty = max(0.5, 0.90 ^ min(times_in_window, 10))
```

- `age_decay` — penalises stale ideas
- `window_penalty` — penalises ideas repeatedly shown but never acted on
- `base_score` — the only field the recorder can adjust, via discrete signals

#### Recorder signals (discrete, bounded — no raw floats)

| Signal | Effect | When |
|---|---|---|
| `boost` | base_score += 0.20 (cap 1.0) | precondition just validated |
| `penalize` | base_score -= 0.15 (floor 0.1) | mechanism undermined |
| `supersede` | status = superseded_by:exp-N | made redundant |
| `revive` | reset to initial_score, times_in_window = 0 | conditions changed |

Max delta capped by control plane regardless of recorder output.

#### Selection (control plane, per researcher call)

```
N_EXPLOIT        = 6   top-6 by runtime_score
N_EXPLORE_RECENT = 2   random from ideas added in last 20 experiments
N_EXPLORE_OLD    = 2   random from ideas added before last 20 experiments
─────────────────────
Total window     = 10  ideas passed to researcher prompt
```

Stratified explore (recent + old strata) ensures both fresh and buried ideas
surface, regardless of pool size. Prevents the lottery problem of pure random
(~1.5% chance per buried idea with 200+ ideas in pool).

#### Garbage collection (every 10 experiments)

Ideas auto-archived when `runtime_score < 0.05` AND `age >= 15 experiments`.
Superseded ideas archived immediately.
Archived ideas move to `idea-store-archive.json` — never deleted, never shown.

#### Initial scores by source

- `source: scout` (literature-backed) → `initial_score = 0.80`
- `source: recorder` (experiment-inferred) → `initial_score = 0.50`

---

### Files

```
sssp/
  mbmm.py                  ← standalone module (scoring, selection, GC, migration)
  idea-store.json          ← replaces unexplored-ideas.md
  idea-store-archive.json  ← GC'd ideas (never shown, never deleted)
  unexplored-ideas.md      ← kept as read-only archive post-migration
  mbmm-plan.md             ← this document
```

#### `mbmm.py` public API

```python
load_store()                                    → list of idea dicts
load_idea_window(current_exp)                   → (selected, ids)
mark_ideas_shown(idea_ids, current_exp)         → None
format_window_for_prompt(selected, current_exp) → str
apply_signals(signals, new_entries, current_exp)→ {signals_applied, ideas_added}
run_gc(current_exp)                             → int (archived count)
migrate_from_flat_file(md_path)                 → int (migrated count)
compute_runtime_score(idea, current_exp)        → float
```

---

### What changed in the system

| Component | Change |
|---|---|
| `mbmm.py` | new module — all scoring/selection/GC logic |
| `control_plane.py` | calls `load_idea_window()` in `run_debate`; calls `apply_signals()` in `call_recorder`; migration check at startup; GC every 10 exps |
| `researcher_a.md` | `{{UNEXPLORED_IDEAS}}` → `{{IDEA_WINDOW}}` (10-idea compact window) |
| `recorder.md` | new output format: `<idea_signals>` + `<new_idea>` + `<do_not_repeat_entry>` |
| `literature_scout.md` | analyze phase now also outputs `<new_ideas>` (JSON, initial_score=0.8) |
| `WRITE_PERMISSIONS` | `idea-store.json` is control-plane only — agents return payloads, never write directly |

---

### Key design decisions from review

1. **Scoring formula does not conflict with recorder**: formula governs `runtime_score`
   (computed, never stored); recorder adjusts `base_score` (stored, bounded).

2. **`times_in_window` wires the "shown but ignored" signal**: window_penalty = 0.90^n
   prevents high-scored ideas from hogging top slots indefinitely.

3. **Recorder emits discrete signals, not raw floats**: prevents LLM from directly
   setting arbitrary scores. All delta bounds enforced by `mbmm.apply_signals()`.

4. **Migration is deterministic regex, not LLM**: count validation (ideas in vs. out)
   ensures no silent data loss.

5. **Stratified epsilon-greedy over pure random**: with 200+ ideas, pure random gives
   ~1.5% per buried idea. Stratification guarantees old and recent ideas both surface.

---

### Known limitations / future work

- Exploration agent (`run_exploration`) still receives `UNEXPLORED_IDEAS` from flat file.
  Extend MBMM to exploration agent in a follow-up.
- No idea deduplication at insertion time — recorder may add near-duplicate entries
  for the same mechanism. Could add an embedding-based similarity check later.
- Score model is heuristic, not a true bandit (no reward signal per arm). Could
  upgrade to UCB or Thompson sampling once reward signal is well-defined.
