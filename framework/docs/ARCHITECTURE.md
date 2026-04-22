# Architecture

How the AutoResearch loop works.

---

## Overview

AutoResearch is an autonomous iterative optimization loop. It uses LLM agents
to propose, debate, implement, and evaluate changes to a measurable artifact.
Each cycle either improves the artifact (kept) or restores the backup (discarded).

```
┌─────────────────────────────────────────────────────────────────┐
│                     Control Plane (Python)                      │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐  │
│  │ Debate   │──▶│ Implement│──▶│  Benchmark │──▶│  Verify  │  │
│  │ A ↔ B   │   │ Agent    │   │  (shell)   │   │  (shell) │  │
│  └──────────┘   └──────────┘   └────────────┘   └──────────┘  │
│       │                                               │         │
│       ▼                                               ▼         │
│  ┌──────────┐                               ┌──────────────┐   │
│  │ MBMM     │◀──────────────────────────────│ Attribution  │   │
│  │ Idea Bank│                               │ Agent        │   │
│  └──────────┘                               └──────────────┘   │
│                                                                 │
│  ┌──────────────┐   ┌────────────────┐                         │
│  │ Exploration  │   │ Literature     │                         │
│  │ Agent (UCB)  │   │ Scout (arXiv)  │                         │
│  └──────────────┘   └────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agents

### Researcher A
Proposes one experiment from the idea bank. Draws on domain context, recent
results, and the current master. Uses MBMM-scored idea window: top-N by UCB
score + epsilon-greedy random selection.

### Researcher B
Challenges the proposal on mechanism soundness, conditions validity, signal
realism, and implementation risk. Can mark APPROVED, CHALLENGED (Researcher A
revises), or FATAL (experiment abandoned).

### Implementation Agent
Takes the approved proposal and produces a new version of the artifact (full
file, not a patch) plus updated params.json. Outputs via XML tags.

### Attribution Agent
After each experiment, attributes the outcome to ideas in the idea bank. Emits
MBMM signals (boost, penalize, supersede, revive) and optionally adds a new idea
or a do-not-repeat entry.

### Exploration Agent
Runs a targeted mini-experiment cycle with a fixed budget when the main loop
needs to characterize a region of the search space. Two phases: plan (select
idea + design runs) and report (synthesize findings).

### Literature Scout
Periodically searches arXiv for new papers relevant to the optimization problem.
Extracts implementable ideas and adds them to the idea bank at a high initial
score. Two phases: queries (generate targeted search terms) and analyze (extract
experiments from retrieved papers).

---

## MBMM (Multi-Bandit Memory Management)

The idea bank is a scored JSON store. Each idea has a `runtime_score` computed
from UCB1 blended with a heuristic as citation count grows:

```
Phase 1 (< UCB_MIN_OBSERVATIONS citations):
  runtime_score = base_score × age_decay × window_penalty

Phase 2 (>= UCB_MIN_OBSERVATIONS citations):
  UCB1 = (times_kept / times_cited) + C × sqrt(ln(N_total) / times_cited)
  runtime_score = (1 - blend) × heuristic + blend × UCB1
```

Selection window: `N_EXPLOIT` top-scored ideas + `N_EXPLORE_RECENT` random
recent + `N_EXPLORE_OLD` random old.

---

## Loop schedule

```
Every iteration:
  1. Debate (Researcher A proposes, Researcher B challenges)
  2. Implementation agent implements approved proposal
  3. Benchmark script runs (measures metric)
  4. Verifier script runs (checks correctness)
  5. Keep or discard (update master.json / restore backup)
  6. Attribution agent updates idea bank

Every explore_every_N iterations:
  7. Exploration agent runs a focused mini-experiment cycle

Every scout_every_N iterations:
  8. Literature scout fetches arXiv papers and adds ideas
```

---

## State files

All state is written to the **instance directory** (not the framework directory).
See INSTANTIATION_GUIDE.md for the full list.

---

## XML tag protocol

See `XML_TAG_PROTOCOL.md` for the complete list of XML tags used between agents
and the control plane.

---

## Pre-render calling convention

`domain_context.md` is pre-rendered before it is injected into framework
templates. The sequence:

1. `domain_context.md` is read from the instance's `agent_prompts/` directory.
2. Instance values (`{{METRIC_NAME}}`, `{{ARTIFACT_NAME}}`, etc.) are substituted.
3. Any remaining `{{VAR}}` patterns raise `ValueError` (injection vector defense).
4. The rendered string becomes `{{DOMAIN_CONTEXT}}` in framework templates.

This ensures domain_context.md cannot inject framework-level variables (like
`{{GOAL_MD}}`) into prompts, and that all instance-specific values flow through
a single source of truth (instance.json).
