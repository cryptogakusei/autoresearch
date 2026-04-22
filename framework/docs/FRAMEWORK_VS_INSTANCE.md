# Framework vs Instance

What is generic (part of this framework) vs what you provide (your instance).

---

## Layer 1 — Pure Framework (never touch)

These components are generic and reusable across any optimization problem:

| Component | What it does |
|-----------|-------------|
| Loop orchestration | debate → implement → benchmark → verify → keep/discard |
| MBMM scoring | UCB1 idea scoring, window selection, GC, citation tracking |
| Exploration budget management | when to explore, how many runs |
| do-not-repeat mechanism | prevents re-proposing exhausted directions |
| Progress tracking | results.tsv, progress.log, exploitation-progress.json |
| Rollback on failure | restore backup on benchmark/verifier fail |
| Template rendering | `render_template_for_phase()`, single-pass substitution |
| Agent calling | `call_agent()`, streaming, token limits |
| XML tag parsing | `extract_xml_tag()` |

---

## Layer 2 — Framework Plumbing (instance-specific values, framework structure)

These are the seam constants in `control_plane.py`. They start with hardcoded
defaults but are overridden by `instance.json` at startup via `apply_instance_config()`:

| Constant | instance.json field | Default (SSSP) |
|----------|---------------------|----------------|
| `METRIC_NAME` | `metric_name` | `"runtime_ms"` |
| `METRIC_DIRECTION` | `metric_direction` | `"lower_is_better"` |
| `ARTIFACT_XML_TAG` | `artifact_xml_tag` | `"sssp_cpp"` |
| `ARTIFACT_SNAPSHOT_NAME` | `artifact_snapshot_name` | `"sssp.cpp"` |
| `BENCHMARK_SCRIPT` | `benchmark` | `"autoresearch.sh"` |
| `VERIFIER_COMMAND` | `verifier_command` | `["bash", "verifier/verify.sh"]` |
| `VERDICT_OUTPUT_PATH` | `verdict_output_path` | `verifier/verdict.json` |
| `FALLBACK_QUERIES` | `fallback_queries` | SSSP-specific strings |
| `MODEL` | `model` | `"claude-opus-4-6"` |
| `SCHEDULE_*` | `schedule.*` | 5/2/2/10 |

---

## Layer 3 — Instance (you provide)

Everything in your instance directory:

| File | What it contains |
|------|-----------------|
| `instance.json` | All configuration for your problem |
| `src/YOUR_ARTIFACT` | The artifact being optimized |
| `params.json` | Tunable parameters |
| `benchmark.sh` | Compile + run + emit METRIC lines |
| `verifier/verify.sh` | Run correctness check + write verdict.json |
| `agent_prompts/domain_context.md` | Domain knowledge for agents |
| `goal.md` | Plain-text optimization goal |
| `data/` | Input data (never written by agents — in `hard_blocked`) |

---

## What agents can and cannot see

Agents receive prompts. They do NOT have file system access. The control plane:
1. Reads files, renders templates, sends prompts to agents
2. Parses agent responses (via XML tags)
3. Writes files on behalf of agents (with permission enforcement)

### Agent write permissions

| Agent | Can write |
|-------|-----------|
| researcher-a | `proposal.md` |
| researcher-b | `objections.md` |
| implementation | artifact, `params.json`, `experiment-result.md` |
| exploration | artifact, `params.json`, `exploration-result.md` |
| attribution | `unexplored-ideas.md`, `do-not-repeat.md` |
| literature-scout | `context/references.md` |
| control-plane | anything (unrestricted — manages state files) |

`hard_blocked` paths (defined in instance.json) can never be written by any agent.

---

## Adding a new problem: checklist

- [ ] Copy `autoresearch/template/` to your instance directory
- [ ] Fill in all required fields in `instance.json`
- [ ] Implement `benchmark.sh` with correct METRIC output
- [ ] Implement `verifier/verify.sh` with correct verdict.json output
- [ ] Write `agent_prompts/domain_context.md` with domain knowledge
- [ ] Write `goal.md` with your optimization goal
- [ ] Run `python3 control_plane.py --instance-dir ./myproject --setup`
- [ ] Verify baseline appears in `master.json`
- [ ] Run one experiment: `python3 control_plane.py --instance-dir ./myproject --once`
