# Instantiation Guide

How to use this framework for a new optimization problem.

---

## Prerequisites

- Python 3.9+
- `anthropic` Python package: `pip install anthropic`
- `ANTHROPIC_API_KEY` environment variable set
- A measurable artifact you want to optimize (e.g., a C++ file, a Python script)
- A benchmark that measures your metric (runtime, accuracy, etc.)
- A verifier that checks correctness

---

## Step-by-step

### 1. Copy the template

```bash
cp -r autoresearch/template/ autoresearch/myproject/
cd autoresearch/myproject/
```

### 2. Fill in instance.json

Open `instance.json` and fill in every field. The schema:

| Field | Required | Description |
|-------|----------|-------------|
| `artifact` | yes | Path to the file being optimized (relative to instance dir) |
| `artifact_snapshot_name` | yes | Filename for snapshots (e.g. `"myfile.cpp"`) |
| `artifact_language` | no | Language string for prompts (e.g. `"C++17"`) |
| `artifact_xml_tag` | yes | XML tag name agents use to wrap artifact content (e.g. `"myfile_cpp"`) |
| `params` | yes | Path to params.json (relative to instance dir) |
| `build_command` | no | Build command string (for prompts only) |
| `benchmark` | yes | Path to benchmark script (relative to instance dir) |
| `verifier_command` | yes | Command to run verifier (e.g. `["bash", "verifier/verify.sh"]`) |
| `verdict_output_path` | yes | Where verifier writes verdict.json (relative to instance dir) |
| `metric_name` | yes | Name of the metric as printed in METRIC lines (e.g. `"runtime_ms"`) |
| `metric_direction` | yes | `"lower_is_better"` or `"higher_is_better"` |
| `benchmark_description` | no | Human prose describing the benchmark |
| `correctness_constraint` | no | Human prose describing correctness |
| `fallback_queries` | yes | arXiv search queries for literature scout |
| `model` | no | Anthropic model ID (default: `"claude-opus-4-6"`) |
| `schedule.explore_every_N` | no | Run exploration every N experiments (default: 5) |
| `schedule.explore_budget` | no | Runs per exploration cycle (default: 2) |
| `schedule.max_debate_rounds` | no | Max researcher debate rounds (default: 2) |
| `schedule.scout_every_N` | no | Run literature scout every N experiments (default: 10) |
| `schedule.recent_experiments_window` | no | Recent experiment summaries injected into Researcher A (default: 5; set 0 to disable) |
| `schedule.recent_experiments_max_lines_per` | no | Max lines extracted per experiment summary (default: 50) |
| `hard_blocked` | no | Paths agents can never write (relative to instance dir) |

### 3. Implement benchmark.sh

The benchmark script must:
1. Compile/prepare your artifact
2. Run it on your test input
3. Print `METRIC metric_name=value` to stdout for the primary metric (and optionally secondary metrics)
4. Optionally print `PROFILE key=value` lines for auxiliary performance data (cache misses, memory, etc.) — these are injected into agent prompts as context but do not affect keep/discard decisions
5. Exit 0 on success, non-zero on failure

```bash
# Example — primary metric (required)
echo "METRIC runtime_ms=42.5"

# Optional profile lines — at most 20; non-numeric values are ignored
echo "PROFILE cache_misses=4821903"
echo "PROFILE peak_memory_kb=102400"
```

The `metric_name` value must exactly match `instance.json → metric_name`. Profile keys can be any identifier — agents see them as context.

### 4. Implement verifier/verify.sh

The verifier script must:
1. Run your artifact and compare output against a reference
2. Write `verdict.json` to `verifier/verdict.json`:
   ```json
   {"status": "PASS", "detail": "...", "failures": []}
   ```
3. Exit 0 (even on FAIL) unless you cannot produce a verdict at all

### 5. Write agent_prompts/domain_context.md

Add domain-specific knowledge the agents need:
- What the artifact does and why the metric matters
- Known good ideas and bad ideas for this domain
- Structural properties of your benchmark
- Key papers or prior work

**Rule:** Only write content here that has no instance.json field. Use
`{{METRIC_NAME}}`, `{{ARTIFACT_NAME}}`, `{{BENCHMARK_DESCRIPTION}}` for values
already in instance.json — never hardcode them.

### 6. Write goal.md

Describe your optimization goal in plain text. This is injected into researcher
and exploration prompts as context. It can overlap with domain_context.md but
goal.md is typically shorter and more directive.

### 7. Run setup

```bash
python3 control_plane.py --instance-dir ./myproject --setup
```

This runs your benchmark once to establish the baseline in `master.json` and
initializes state files.

### 8. Run the loop

```bash
python3 control_plane.py --instance-dir ./myproject
```

Or run one experiment at a time:

```bash
python3 control_plane.py --instance-dir ./myproject --once
```

---

## State files (all written to instance directory)

| File | Purpose |
|------|---------|
| `master.json` | Current best result |
| `results.tsv` | Full experiment history |
| `idea-store.json` | MBMM scored idea bank |
| `idea-store-archive.json` | Archived (low-score) ideas |
| `exploitation-progress.json` | Loop schedule state |
| `do-not-repeat.md` | Exhausted directions ledger |
| `artifacts/exp-N/` | Snapshots of each experiment |

---

## Variable reference

All variables injected into agent prompts:

| Template variable | Source |
|-------------------|--------|
| `{{ARTIFACT_NAME}}` | `artifact_snapshot_name` |
| `{{ARTIFACT_LANGUAGE}}` | `artifact_language` |
| `{{ARTIFACT_XML_TAG}}` | `artifact_xml_tag` |
| `{{METRIC_NAME}}` | `metric_name` |
| `{{METRIC_DIRECTION}}` | `metric_direction` (transformed to prose) |
| `{{BUILD_COMMAND}}` | `build_command` |
| `{{BENCHMARK_DESCRIPTION}}` | `benchmark_description` |
| `{{CORRECTNESS_CONSTRAINT}}` | `correctness_constraint` |
| `{{DOMAIN_CONTEXT}}` | Pre-rendered `agent_prompts/domain_context.md` |
