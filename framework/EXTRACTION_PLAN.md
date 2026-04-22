# AutoResearch Framework — Extraction Plan (v3)

> Revised after three rounds of independent review (systems-architect + architecture-reviewer).
> All v1, v2, and v3 review findings are incorporated. Major corrections marked ⚠️.

---

## 1. What Is Being Extracted

AutoResearch is an autonomous iterative optimization loop that uses LLM agents to
improve any measurable artifact. It was developed inside the SSSP project but the
core machinery is domain-agnostic.

The extraction separates two things:

- **Framework**: the generic loop, idea management, agent orchestration, rollback
  safety, and progress tracking — reusable across any problem
- **Instance**: the artifact, benchmark harness, verifier, and domain-specific
  prompt language — provided by the problem owner

The SSSP project becomes the reference instance that demonstrates how to use the
framework.

---

## 2. Target Directory Structure

```
autoresearch/
  framework/                    ← this repo (the extracted framework)
    core/
      control_plane.py          generalized orchestration loop
      mbmm.py                   Multi-Bandit Memory Management (needs path fix — see §6)
      __init__.py
    templates/                  ← default prompts (instance injects domain_context.md)
      agent_prompts/
        researcher_a.md         base template with {{DOMAIN_CONTEXT}} injection block
        researcher_b.md
        implementation.md
        attribution.md
        exploration.md
        literature_scout.md
    schema/
      instance.schema.json      what every instance must provide (validated at startup)
      idea-store.schema.json    idea entry format
      master.schema.json        master.json format
      verdict.schema.json       verifier output contract (see §5.2)
      benchmark-output.schema   METRIC key=value format contract (see §5.1)
    docs/
      INSTANTIATION_GUIDE.md    step-by-step: how to use this for a new problem
      ARCHITECTURE.md           how the loop works, agent decision flow
      XML_TAG_PROTOCOL.md       every XML tag: which agent produces, which function consumes
      FRAMEWORK_VS_INSTANCE.md  what is generic vs what you provide
    EXTRACTION_PLAN.md          this file

  sssp/                         ← reference instance (untouched during extraction)
    src/sssp.cpp
    autoresearch.sh
    data/
    instance.json               NEW: instance config (replaces hardcoded paths)
    agent_prompts/
      domain_context.md         NEW: SSSP domain knowledge block, injected into templates
    goal.md
    params.json
    verifier/
      verify.sh                 NEW: thin wrapper implementing verifier contract (see §5.2)

  template/                     ← blank instance (copy to start a new project)
    instance.json               fill in all fields per schema
    benchmark.sh                compile + run + print METRIC lines
    goal.md                     describe your problem
    params.json                 your tunable parameters
    verifier/
      verify.sh                 run reference implementation, write verdict JSON
    agent_prompts/
      domain_context.md         your domain knowledge block
```

---

## 3. Three-Layer Architecture

⚠️ The original plan treated framework and instance as two layers. Both reviewers
identified a critical middle layer that contains most of the extraction work.

### Layer 1 — Pure Framework (truly generic, extract as-is with path fixes)

| Component | What it does |
|-----------|-------------|
| Loop orchestration (debate → implement → benchmark → verify → keep/discard) | Generic |
| Idea management: UCB scoring, windowed selection, GC, citation tracking | Generic |
| Exploration budget management | Generic |
| do-not-repeat.md mechanism | Generic |
| Progress tracking (results.tsv, progress.log, exploitation-progress.json) | Generic |
| Rollback on failure + TimeoutExpired handling | Generic |
| Template rendering: render_template_for_phase() | Generic |
| Agent calling: call_agent() | Generic (Anthropic-coupled — see Risk 3) |

### Layer 2 — Framework Plumbing (generic in structure, instance-specific in values)

⚠️ This layer contains most of the extraction work. These components live in
control_plane.py but contain hardcoded SSSP values that must become config.

| Component | Current SSSP hardcoding | Extraction action |
|-----------|------------------------|-------------------|
| Benchmark invocation | `"autoresearch.sh"`, `"runtime_ms"` metric key | Read from instance.json |
| Verifier invocation | 76-line `run_verifier()` + Docker + DIMACS parsing | `subprocess.run(config["verifier_command"])` — see §5.2 |
| Source ID collection | `collect_source_ids()` reads DIMACS `.ss` format | Move to SSSP verifier/verify.sh |
| Artifact management | `"sssp.cpp"` in save_artifact(), `"sssp_cpp"` XML tag | Read from instance.json |
| Metric comparison direction | `metric_after < metric_before` at **7 sites** (lines 446, 721, 844, 874, 942, 947, 969) plus hardcoded defaults in `load_progress()` | `is_improvement()` + `format_delta()` helpers; update all 7 sites AND `load_progress()` defaults |
| Write permissions | `WRITE_PERMISSIONS` dict hardcodes `SSSP_CPP`, `PARAMS_JSON` | Derive from instance.json |
| Artifact snapshot list | `save_artifact()` snapshots hardcoded filenames | Read from instance.json |
| Literature scout queries | `ARXIV_QUERIES` Python list with SSSP search terms | Move to instance.json `fallback_queries` |
| Session schedule config | `explore_every_N` etc. parsed from goal.md via regex; defaults duplicated in `load_progress()` | Move to instance.json; update both the regex site AND `load_progress()` defaults |
| LLM model + token limits | `MODEL = "claude-opus-4-6"` (line 64), `max_tokens` differs per agent | Read from instance.json |
| master.json metric_name | `update_master()` hardcodes `"runtime_ms"` | Read from instance.json `metric_name` |
| Anthropic client | `client = anthropic.Anthropic()` at module level (line 126) | Lazy-initialize inside `call_agent()` to enable testing |

### Layer 3 — Instance Code (cannot be generalized, stays in instance directory)

| Component | SSSP example |
|-----------|-------------|
| Artifact | src/sssp.cpp |
| Benchmark harness | autoresearch.sh |
| Verifier implementation | verifier/verify.py + dijkstra_ref.cpp + Docker image |
| Data files | data/cal.gr, data/cal.ss |
| Domain prompt language | SSSP-specific wording in agent_prompts/ |
| Reference implementation | dijkstra_ref.cpp |
| params.json schema | {k, t, base_case_n, pivot_rule, pq_type} — opaque blob to framework |

---

## 4. Full instance.json Schema

⚠️ v1 had ~6 fields. v2 expanded to 15+. v3 adds `verdict_output_path` (required by
verifier contract) and clarifies `build_command` scope.

```json
{
  "artifact": "src/sssp.cpp",
  "artifact_xml_tag": "sssp_cpp",
  "artifact_snapshot_name": "sssp.cpp",
  "params": "params.json",
  "benchmark": "autoresearch.sh",
  "verifier_command": "bash verifier/verify.sh",
  "verdict_output_path": "verifier/verdict.json",
  "hard_blocked": ["src/dijkstra_ref.cpp", "verifier/", "data/"],
  "metric_name": "runtime_ms",
  "metric_direction": "lower_is_better",
  "secondary_metrics": ["relaxations", "memory_mb"],
  "model": "claude-opus-4-6",
  "max_tokens_implementation": 32000,
  "max_tokens_default": 8192,
  "fallback_queries": [
    "your domain search query 1",
    "your domain search query 2"
  ],
  "schedule": {
    "explore_every_N": 5,
    "explore_budget": 2,
    "max_debate_rounds": 2,
    "scout_every_M": 3
  },
  "build_command": "g++ -O2 -std=c++17",
  "artifact_language": "C++17",
  "benchmark_description": "DIMACS CAL road network, 5 source nodes, median runtime",
  "correctness_constraint": "exact distance match with reference Dijkstra",
  "benchmark_timeout_seconds": 120,
  "verifier_timeout_seconds": 600
}
```

**Field notes:**

- `artifact_snapshot_name`: the bare filename used when snapshotting. Usually derivable
  via `Path(artifact).name`. Kept as an explicit field because some artifacts have deep
  paths (`src/foo/bar.cpp`) but should snapshot as `bar.cpp`.
- `build_command`: **prompt-only context**. The framework never invokes it directly.
  It is injected into agent prompts as `{{BUILD_COMMAND}}`. Building and running are
  the benchmark script's responsibility.
- `verdict_output_path`: path relative to instance dir where verifier writes verdict.json.
  Framework reads this file after `verifier_command` exits.
- `hard_blocked`: list of paths (relative to instance dir) the framework will refuse to
  write. Enforcement is two-layer: (1) pre-write check in the framework before any file
  write, (2) listed in agent prompts as off-limits. Both layers are required because prompt
  compliance is probabilistic.
  - **Enforcement mechanism:** `write_file(path, content)` in control_plane.py resolves
    `path` relative to `instance_dir`, then checks whether it starts with any entry in
    `hard_blocked` (prefix match after `Path.resolve()`). On violation: raise
    `PermissionError`, log the blocked path, abort the current experiment step, and
    trigger the same rollback path as a failed benchmark. Do not silently skip.
- `benchmark_timeout_seconds`: seconds before the benchmark subprocess is killed. Default
  120. On timeout the experiment is discarded as `TIMEOUT`.
- `verifier_timeout_seconds`: seconds before the verifier subprocess is killed. Default
  600. On timeout the experiment is discarded as `FAIL` with detail `"verifier timed out"`.
- `artifact_language`, `benchmark_description`, `correctness_constraint`: **optional,
  default `""`**. These were absent from previous versions of the schema. Existing
  instances that omit them will not fail validation — `validate_instance_config()` warns
  but continues. Agents will see empty strings, which is preferable to a startup error.
- `params.json` is an **opaque instance-owned JSON blob**. Framework reads it, passes
  it to agents as context, and writes it back. Framework never inspects its contents.

**Field naming convention:** All `instance.json` fields use `snake_case`. The render
pass maps them to `{{UPPER_SNAKE_CASE}}` template variables mechanically (e.g.,
`metric_name` → `{{METRIC_NAME}}`). This convention is documented in
`INSTANTIATION_GUIDE.md`. Any field added to `instance.json` that should be available
as a template variable must be registered in the substitutions dict in `control_plane.py`.

A `validate_instance_config()` function runs at startup and fails fast with a clear
error message if any required field is missing, invalid, or points to a nonexistent path.

---

## 5. Interface Contracts

### 5.1 Benchmark Output Contract

⚠️ v2 listed a `benchmark-output.schema` file but never defined its contents. This is
the primary data contract between instance and framework and must be precise.

**Format:**

```
METRIC <name>=<value>
```

**Rules:**
- One `METRIC` line per metric. The framework scans stdout line by line.
- `<name>` is a bare identifier (no spaces, no quotes). `<value>` is a decimal number
  (integer or float). No spaces around `=`.
- The framework expects exactly one line matching `METRIC <instance.metric_name>=`.
  If zero such lines appear → benchmark result is `None`, experiment is discarded as
  `FAIL-METRIC`. If multiple lines with the same key appear → last one wins.
- Secondary metrics (`instance.secondary_metrics`) are optional. If absent, the
  framework records `null` for those fields.
- If `<value>` is not a valid decimal number (e.g. `NaN`, `fast`, empty string) →
  treat as absent: log a warning, result is `None`, experiment is discarded as
  `FAIL-METRIC`.
- All other stdout lines are ignored (logs, compiler warnings, etc.).
- If the benchmark exits non-zero, result is `None` regardless of stdout content.

**Example valid output:**
```
Compiling...
METRIC runtime_ms=168
METRIC relaxations=4821903
time per source: 168ms
```

### 5.2 Verifier Interface Contract

⚠️ Both reviewers flagged this as P1 in v2. The contract must be fully specified before
any new instance can build a working verifier.

**Invocation:**
```
bash verifier/verify.sh
```
The framework calls `subprocess.run(config["verifier_command"], cwd=instance_dir,
capture_output=True, timeout=config["verifier_timeout_seconds"])`.

**What the verifier can assume is already present when called:**
- The artifact (`config["artifact"]`) is the current candidate version on disk
- The benchmark has already run (distances/output files produced by the benchmark
  are available in their expected locations)
- The instance directory is the working directory

**What the verifier must NOT assume:**
- Any environment variables beyond standard `PATH`
- Docker availability (should degrade gracefully if Docker is absent)
- Any temp directory outside the instance directory

**What the verifier must produce:**
Write a verdict JSON to `config["verdict_output_path"]` before exiting.

**Verdict JSON schema:**
```json
{
  "status": "PASS" | "FAIL",
  "detail": "human-readable summary string",
  "failures": [
    {
      "source": "integer or string ID",
      "expected": "number or string",
      "actual": "number or string"
    }
  ],
  "metrics": {
    "any_key": "<number | string | null>"
  }
}
```
- `status` and `detail` are required.
- `failures` is optional (omit or empty array if none).
- `metrics` is optional; framework logs but does not act on its contents in v1.
- If the verifier exits non-zero AND verdict JSON is absent or malformed, the
  framework treats the result as `FAIL` with detail `"verifier produced no output"`.

**SSSP verify.sh responsibilities** (moves from control_plane.py run_verifier()):
- Call `collect_source_ids()` to get source node list from `.ss` file
- Run reference Dijkstra via dijkstra_ref.cpp or Docker image
- Compare distances from sssp binary output against reference
- Write verdict.json with per-source failure entries on mismatch

---

## 6. Parameterization Points in Agent Prompts

### Injection model

Framework ships structural templates; instances provide only `domain_context.md`
injected at `{{DOMAIN_CONTEXT}}` placeholders. Instances cannot replace structural
templates — only the domain context blocks.

- **Structural logic** (XML output format, phase headers, decision rules, MBMM signal
  schema) stays in framework templates
- **Domain knowledge** (what the artifact does, language, benchmark structure,
  correctness constraint, typical failure modes) lives in `domain_context.md`

This prevents instances from accidentally breaking the XML parsing contract while
still getting full domain customization. Full prompt override is available as an
escape hatch only for edge cases not covered by the injection model.

### Single source of truth: instance.json owns all objective values

⚠️ `instance.json` is the single source of truth for every objective value
(`metric_name`, `metric_direction`, `artifact`, etc.). These values must never be
hardcoded a second time anywhere — including in `domain_context.md`.

**`domain_context.md` is also subject to template variable substitution.** The
framework processes it in two passes before injection:

```
Pass 1: load domain_context.md
        substitute {{METRIC_NAME}}, {{ARTIFACT_NAME}}, etc. from instance.json

Pass 2: load framework template
        substitute {{DOMAIN_CONTEXT}} with the Pass 1 result
        substitute remaining {{VAR}} placeholders
```

This means `domain_context.md` can (and should) reference metric and artifact
values via variables, not hardcoded strings:

```markdown
# domain_context.md  ✓ correct
We are optimizing {{ARTIFACT_NAME}} written in {{ARTIFACT_LANGUAGE}}.
The metric is {{METRIC_NAME}} ({{METRIC_DIRECTION}}).
Correctness constraint: {{CORRECTNESS_CONSTRAINT}}.
```

```markdown
# domain_context.md  ✗ wrong — creates a second source of truth
We are optimizing sssp.cpp written in C++17.
The metric is runtime_ms (lower is better).
Correctness constraint: exact match with reference Dijkstra.
```

If an instance hardcodes these values in `domain_context.md`, changing
`instance.json` will update the control plane logic but silently leave the agents
working with stale information.

**Rendering is a pre-render calling convention, not two separate render functions.**
`render_template_for_phase()` itself does not change. What changes is how callers
build the substitution dict: they must pre-render `domain_context.md` first, then
pass the result as the value of `{{DOMAIN_CONTEXT}}`:

```python
# caller (in control_plane.py) — correct pattern
domain_ctx = render_string(
    read_file(instance_dir / "agent_prompts/domain_context.md"),
    substitutions  # contains METRIC_NAME, ARTIFACT_NAME, etc. from instance.json
)
substitutions["DOMAIN_CONTEXT"] = domain_ctx
prompt = render_template_for_phase(template_path, phase, substitutions)
```

**Injection vector defense:** After pre-rendering `domain_context.md`, the result
must contain no remaining `{{...}}` patterns. If any survive (e.g., an accidental
`{{GOAL_MD}}` in domain_context.md), assert and raise an error before proceeding.
Rationale: Pass 2 would silently substitute framework-internal variables (like
`{{GOAL_MD}}` or `{{RECENT_RESULTS}}`) into the domain context, producing an
agent prompt that leaks framework state into the domain description.

```python
rendered = render_string(domain_context_content, substitutions)
if re.search(r'\{\{[A-Z_]+\}\}', rendered):
    raise ValueError(f"domain_context.md contains unresolved variables after render")
substitutions["DOMAIN_CONTEXT"] = rendered
```

The existing test for "no double-substitution" must verify that a `{{VAR}}` in
`domain_context.md` is resolved exactly once — not zero times (missing from
substitutions dict) and not twice (re-scanned in Pass 2).

### Template variables

**Naming convention:** instance.json fields use `snake_case`. Template variables
use `{{UPPER_SNAKE_CASE}}`. The render pass maps by convention: `metric_name` →
`{{METRIC_NAME}}`, `artifact_xml_tag` → `{{ARTIFACT_XML_TAG}}`, etc. This mapping
is mechanical and must be documented explicitly in `INSTANTIATION_GUIDE.md` to
prevent silent mismatches.

All variables below are sourced from `instance.json` and injected into both
framework templates and `domain_context.md`:

| Variable | SSSP value (rendered) | Source field in instance.json | Notes |
|----------|----------------------|-------------------------------|-------|
| `{{DOMAIN_CONTEXT}}` | rendered domain_context.md block | — (injected after pre-render) | |
| `{{ARTIFACT_NAME}}` | sssp.cpp | `artifact_snapshot_name` | |
| `{{ARTIFACT_LANGUAGE}}` | C++17 | `artifact_language` | optional, default `""` |
| `{{BUILD_COMMAND}}` | g++ -O2 -std=c++17 | `build_command` | prompt-only |
| `{{METRIC_NAME}}` | runtime_ms | `metric_name` | |
| `{{METRIC_DIRECTION}}` | lower is better | `metric_direction` | **transformed** (see below) |
| `{{BENCHMARK_DESCRIPTION}}` | DIMACS CAL road network, 5 source nodes, median runtime | `benchmark_description` | free-text, optional |
| `{{CORRECTNESS_CONSTRAINT}}` | exact distance match with reference Dijkstra | `correctness_constraint` | optional, default `""` |
| `{{ARTIFACT_XML_TAG}}` | sssp_cpp | `artifact_xml_tag` | |

**`{{METRIC_DIRECTION}}` transformation:** `instance.json` stores the machine token
`"lower_is_better"` or `"higher_is_better"`. Agents need human prose. The render
pass must apply a transform before substitution:

```python
METRIC_DIRECTION_DISPLAY = {
    "lower_is_better":  "lower is better",
    "higher_is_better": "higher is better",
}
substitutions["METRIC_DIRECTION"] = METRIC_DIRECTION_DISPLAY[config["metric_direction"]]
```

This is the only field that requires a transformation. All other variables are
injected verbatim from `instance.json`.

**`benchmark_description`** is deliberately free-text. It absorbs the dataset name,
query structure, and aggregation method (e.g., "5 source nodes, median runtime") as
a single prose string. Splitting these into separate structured fields would over-engineer
the schema for no benefit — agents consume it as prose anyway.

**Three fields (`artifact_language`, `benchmark_description`, `correctness_constraint`)
are optional with empty-string defaults.** They were missing from previous versions.
Existing instances that omit them will not break — `validate_instance_config()` warns
but does not fail. Agents will see empty strings for those variables, which is
preferable to a startup failure on a field that was not previously required.

### XML Tag Protocol

⚠️ P1 risk. The implementation agent outputs `<sssp_cpp>...</sssp_cpp>` and
control_plane.py calls `extract_xml_tag(impl_response, "sssp_cpp")`. These form an
implicit contract — changing one without the other silently drops the implementation.

**`XML_TAG_PROTOCOL.md` must be written before Step 3** (not at Step 11 as originally
sequenced). It enumerates every tag, which agent produces it, and which control plane
function consumes it.

Tags split into two categories:

**Framework-stable tags** (immutable protocol — never change, never templatize):
`<idea_signals>`, `<new_idea>`, `<do_not_repeat_entry>`, `<new_references>`,
`<new_ideas>`, `<search_queries>`, `<selected_idea>`, `<params_json>`

**Instance-configurable tag** (driven by `artifact_xml_tag` in instance.json):
`<sssp_cpp>` → generalizes to `<{{ARTIFACT_XML_TAG}}>`

### Per-prompt changes needed

Two categories of SSSP-specific content in prompts:

**A — Replace with template variable** (value lives in instance.json):

| Prompt | Hardcoded content | Replace with |
|--------|-------------------|--------------|
| researcher_a.md | "CAL — USA-road-d.CAL, 1,890,815 nodes, 4,657,742 edges" | `{{BENCHMARK_DESCRIPTION}}` |
| researcher_b.md | "road networks and DIMACS CAL specifically" | `{{BENCHMARK_DESCRIPTION}}` |
| implementation.md | `<sssp_cpp>` XML tag | `<{{ARTIFACT_XML_TAG}}>` |
| implementation.md | `g++ -O2 -std=c++17` compile command | `{{BUILD_COMMAND}}` |
| exploration.md | "sssp.cpp", "DIMACS road networks", CAL node/edge counts | `{{ARTIFACT_NAME}}`, `{{BENCHMARK_DESCRIPTION}}` |
| attribution.md | `<sssp_cpp>` references | `<{{ARTIFACT_XML_TAG}}>` |
| attribution.md | `"sssp.cpp or params.json"` | `{{ARTIFACT_NAME}}` |
| attribution.md | `"runtime_ms"` | `{{METRIC_NAME}}` |
| literature_scout.md | "SSSP", "shortest path" seed terms | `{{METRIC_NAME}}`, `{{ARTIFACT_NAME}}` |

**B — Move wholesale to domain_context.md** (SSSP-specific prose, not expressible
as a single structured field):

| Prompt | Content to move |
|--------|----------------|
| researcher_a.md | Idea category list (priority queue, pivot rule, BMSSP stub, etc.) |
| researcher_a.md | Paper citations (arXiv:2504.17033, etc.) |
| researcher_a.md | Road network structural properties (sparse, low-degree, etc.) |
| researcher_b.md | Graph theory knowledge specific to road networks |
| literature_scout.md | Domain-specific search seed phrases beyond metric/artifact names |

After migration, framework templates reference only template variables. All SSSP
domain knowledge lives in `sssp/agent_prompts/domain_context.md`.

---

## 7. What Needs Fixing Before Extraction

### mbmm.py — path coupling (P1)

Lines 44-46 resolve `IDEA_STORE_PATH` and `IDEA_ARCHIVE_PATH` relative to `__file__`:

```python
_BASE = Path(__file__).parent.resolve()
IDEA_STORE_PATH = _BASE / "idea-store.json"
IDEA_ARCHIVE_PATH = _BASE / "idea-archive.json"
```

If mbmm.py lives in `framework/core/`, both paths resolve to `framework/core/` instead
of the instance directory.

**Fix:** Add `init(instance_dir: Path)` that sets all store paths. Call it from
control_plane.py immediately after loading instance config.

### save_artifact() — hardcoded filenames (P2)

`save_artifact()` hardcodes `sssp.cpp` and `params.json` as snapshot filenames.
After extraction it must read these from instance config.

### Anthropic client — module-level instantiation (P3)

`client = anthropic.Anthropic()` at line 126 runs at import time. This prevents
unit testing of any function that calls `call_agent()` without a real API key.

**Fix:** Lazy-initialize inside `call_agent()`:
```python
_client = None
def call_agent(...):
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    ...
```

### What IS genuinely generic (extract as-is after above fixes):

- UCB scoring logic in mbmm.py
- Loop orchestration decisions (keep/discard, exploration budget, scout schedule)
- GC logic, window selection, score decay
- do-not-repeat mechanism
- Progress log format (results.tsv, progress.log)
- Template rendering: render_template_for_phase()

---

## 8. The Template Instance

A blank `template/` directory gives new users a starting point:

```
template/
  instance.json           fill in all fields (see §4 schema)
  benchmark.sh            compile + run + print "METRIC key=value" lines
  goal.md                 describe your problem
  params.json             your tunable parameters
  verifier/
    verify.sh             run reference implementation, write verdict JSON
  agent_prompts/
    domain_context.md     your domain: known failure modes, useful literature,
                          domain-specific constraints not expressible as a
                          single field in instance.json
                          MUST use {{METRIC_NAME}}, {{ARTIFACT_NAME}}, etc.
                          for any value already declared in instance.json.
                          Do NOT hardcode metric names, artifact names, or
                          optimization direction here.
```

---

## 9. Framework Invocation

⚠️ v2 deferred this to "Step 9, Size: S" with no design. This decision shapes
instance portability and must be made explicit.

**Decision:** Framework is invoked with an `--instance-dir` CLI argument:

```bash
python -m autoresearch.core.control_plane --instance-dir ./sssp run
python -m autoresearch.core.control_plane --instance-dir ./sssp setup
```

**Path resolution rules:**
- All paths in `instance.json` are relative to the instance directory.
- Framework resolves them as `Path(instance_dir) / config["artifact"]`, etc.
- `instance.json` itself is always `<instance_dir>/instance.json`.
- State files (`idea-store.json`, `master.json`, etc.) are written to the instance
  directory, not the framework directory.

**Import/packaging:** Python relative import. Framework is a package at
`autoresearch/framework/`. Instances are not packages — they are directories passed
via `--instance-dir`. No pip install required; instances add the framework root to
`PYTHONPATH` or use a thin launcher script.

---

## 10. Decided Design Questions

⚠️ v2 listed these as "open questions with recommendations." Both reviewers noted the
contradiction: Q2/Q3/Q4 had recommended answers but were still framed as decisions to
make. These are now decisions.

**D1: Instance config format** — single `instance.json`. Validated at startup by
`validate_instance_config()`.

**D2: Agent prompt strategy** — injection model. Framework templates ship with
`{{DOMAIN_CONTEXT}}` blocks; instances provide only `domain_context.md`. Structural
logic (XML format, phase markers, MBMM signal schema) is framework-owned and immutable.

**D3: Verifier interface** — subprocess + verdict JSON. See §5.2.

**D4: Benchmark interface** — `METRIC key=value` format. See §5.1. `metric_name` is
configurable via instance.json.

**D5: Prompt resolution order** — framework templates + instance injection. Instances
cannot replace structural templates.

**D6: Schedule config location** — `instance.json` `schedule` block. Migrate both the
goal.md regex site AND the `load_progress()` hardcoded defaults.

**D7: params.json contract** — opaque blob. Framework reads, passes to agents, writes
back. Framework never inspects contents.

**D8: unexplored-ideas.md migration** — keep as utility function, not invoked from
main loop. Instance owners migrating from flat files call it explicitly.

**D9: Recorder Agent renamed to Attribution Agent.**
"Recorder" implies logging. The agent's actual job is determining which ideas from
the idea store caused the experimental outcome — that is attribution, not recording.
Rename: `recorder.md` → `attribution.md`, "Recorder Agent" → "Attribution Agent"
everywhere in prompts, docs, and code.

**Deferred to v2 of framework:**

**Q5: Multi-artifact support** — out of scope. Single-artifact is clean and covers 95%
of use cases.

---

## 11. Migration Steps (Ordered)

**Rollback protocol:** Each step must be a self-contained commit. If a step breaks
something, revert to the previous commit — do not proceed to the next step with a
broken state. Steps 0–3 and Step 8 are S-sized and can each be a single commit.
Steps 4–7 and 9 are M-sized; commit at each sub-task boundary.


### Step 0 — Write XML_TAG_PROTOCOL.md
Document every XML tag (producer, consumer). Establish framework-stable tag list as
immutable protocol. Do this before any code moves so the contract is explicit.
Size: S. **Must precede Step 3.**

### Step 1 — Fix mbmm.py path coupling
Add `init(instance_dir: Path)` to mbmm.py. Call it from control_plane.py after loading
instance config. Verify both `IDEA_STORE_PATH` and `IDEA_ARCHIVE_PATH` resolve correctly.
Size: S.

### Step 2 — Introduce metric direction helpers
Add `is_improvement(before, after, direction) -> bool` and
`format_delta(before, after, direction) -> str`. Audit and replace all **7** comparison
sites (lines 446, 721, 844, 874, 942, 947, 969) AND the hardcoded defaults in
`load_progress()`.
Size: S.

### Step 3 — Parameterize artifact XML tag
Update `run_implementation()` and `run_exploration()` to use
`extract_xml_tag(response, config["artifact_xml_tag"])` instead of hardcoded `"sssp_cpp"`.
Update framework `implementation.md` template to use `<{{ARTIFACT_XML_TAG}}>`.
Size: S.

### Step 4 — Extract run_verifier() to instance
Replace the 76-line SSSP-specific `run_verifier()` + `collect_source_ids()` with:
```python
subprocess.run(config["verifier_command"], cwd=instance_dir, ...)
verdict = json.load(open(instance_dir / config["verdict_output_path"]))
```
Move all SSSP verifier logic (source collection, Docker, fallback, distance comparison)
into `sssp/verifier/verify.sh`. Follow the contract in §5.2 exactly.
Size: M.

### Step 5 — Extract remaining Layer 2 coupling points

⚠️ This step aggregates 9 distinct changes across different subsystems. Treat each
as a separate sub-task; complete and verify each before moving to the next.

| Sub-task | Location in control_plane.py | Change |
|----------|------------------------------|--------|
| 5a. Literature scout queries | `ARXIV_QUERIES` constant (top of file) | Replace with `config["fallback_queries"]` |
| 5b. Write permissions | `WRITE_PERMISSIONS` dict | Derive from `[config["artifact"], config["params"]]` |
| 5c. Artifact snapshot filenames | `save_artifact()` | Read artifact name from `config["artifact_snapshot_name"]` |
| 5d. LLM model + token limits | `MODEL = "claude-opus-4-6"` (line 64), `max_tokens` per agent | Read from `config["model"]`, `config["max_tokens_implementation"]`, `config["max_tokens_default"]` |
| 5e. Metric name in update_master() | `"runtime_ms"` literal in `update_master()` | Replace with `config["metric_name"]` |
| 5f. Schedule config — goal.md regex site | `explore_every_N` etc. parsed via regex from goal.md | Read from `config["schedule"]` |
| 5g. Schedule config — load_progress() defaults | Hardcoded default values in `load_progress()` | Replace with `config["schedule"]` defaults |
| 5h. Benchmark script path | `"autoresearch.sh"` literal in `run_benchmark()` | Replace with `config["benchmark"]` |
| 5i. hard_blocked enforcement | No current enforcement | Add prefix-match pre-write check to `write_file()` per §4 spec; wire `benchmark_timeout_seconds` and `verifier_timeout_seconds` from config |
| 5j. New instance.json fields | Missing fields | Add `artifact_language`, `benchmark_description`, `correctness_constraint` to schema; optional-with-defaults in `validate_instance_config()` |
| 5k. domain_context.md linter | No current enforcement | Add `validate_domain_context(instance_dir, config)` that warns (not errors) when any `instance.json` string value appears literally in `domain_context.md`; run at startup after `validate_instance_config()` |

Size: M (commit each sub-task separately).

### Step 6 — Parameterize agent prompts
Replace all SSSP-specific wording with `{{variables}}` from §6. Create
`{{DOMAIN_CONTEXT}}` injection blocks in all framework templates.
Size: M.

### Step 7 — Write tests
Unit tests for at minimum:
- `compute_runtime_score()` with both metric directions
- `apply_signals()`, `record_citation_outcome()`
- `render_template_for_phase()` — verify phase stripping, no double-substitution
- `validate_instance_config()` — happy path + missing required fields
- `is_improvement()` + `format_delta()` with lower/higher directions
- Config loader with a mock instance.json
- Benchmark output parser — enumerate all edge cases:
  - Zero METRIC lines matching metric_name → `None`, `FAIL-METRIC`
  - Duplicate keys → last one wins
  - Malformed value (`NaN`, empty, string) → `None`, `FAIL-METRIC`, warning logged
  - Negative value → accepted (valid for metrics like delta)
  - Scientific notation (`1.68e2`) → accepted as float
  - Empty stdout → `None`, `FAIL-METRIC`
  - Interleaved stderr lines (stderr is separate stream, not in stdout scan)
  - METRIC line present but benchmark exits non-zero → `None` regardless
- Verifier: non-zero exit with no verdict file → `FAIL`, detail `"verifier produced no output"`
- Verifier: verdict file present but malformed JSON → `FAIL`, detail `"verifier produced no output"`
- `hard_blocked` enforcement: write to blocked path raises `PermissionError`, triggers rollback
- Pre-render calling convention:
  - `{{METRIC_NAME}}` in `domain_context.md` is resolved from instance.json before injection
  - `{{DOMAIN_CONTEXT}}` in framework template receives the pre-rendered result
  - A `{{VAR}}` present in `domain_context.md` is resolved exactly once (no double-substitution)
  - A hardcoded value in `domain_context.md` matching an instance.json field is NOT detected at runtime (warn only via linter — see step 5k)
  - Injection vector: `domain_context.md` containing `{{GOAL_MD}}` raises `ValueError` before prompt is built
- `{{METRIC_DIRECTION}}` transform: `"lower_is_better"` → `"lower is better"`, `"higher_is_better"` → `"higher is better"` — verify both directions render correctly in prompts
- `validate_domain_context()` linter: warns on literal `"runtime_ms"` appearing in domain_context.md when `metric_name` is `"runtime_ms"`
Size: M.

### Step 8 — Lazy-initialize Anthropic client
Move `client = anthropic.Anthropic()` from module level into `call_agent()` with a
module-level `_client = None` guard. Confirms tests from Step 7 can run without API key.
Size: S.

### Step 9 — Update SSSP instance
Move all SSSP-specific wording into `sssp/agent_prompts/domain_context.md`.
Add `sssp/instance.json`. Add `sssp/verifier/verify.sh`. Add `--instance-dir` launcher.
Confirm SSSP instance config passes `validate_instance_config()`.
Size: M.

### Step 10 — Define import/packaging mechanism
Add `__init__.py`. Document invocation as `python -m autoresearch.core.control_plane
--instance-dir ./sssp`. Confirm path resolution works from any working directory.
Size: S.

### Step 11 — Validate with fully separated system

Run the following scenarios and confirm the listed pass criteria:

| Scenario | Pass criteria |
|----------|--------------|
| 2 exploitation cycles | `results.tsv` gains 2 rows; `master.json` updated; `idea-store.json` written to instance dir, not framework dir |
| 1 exploration cycle | `exploration-result.md` written to instance dir; budget counter decremented |
| 1 scout cycle | `idea-store.json` updated with new entries sourced from instance `fallback_queries` |
| Rollback on failed benchmark | `sssp.cpp` and `params.json` restored to pre-experiment state; `results.tsv` row shows `discarded` |
| hard_blocked write attempt | Attempt to write to a `hard_blocked` path raises `PermissionError`, experiment aborts, no file change at blocked path |
| idea-store location | `ls framework/core/` shows no `idea-store.json`; `ls sssp/` shows `idea-store.json` |

Note: LLM non-determinism means agent outputs will differ — validate structure and state
transitions, not exact content values.
Size: M.

### Step 12 — Write template + docs
Write `template/`, `INSTANTIATION_GUIDE.md`, `ARCHITECTURE.md`,
`FRAMEWORK_VS_INSTANCE.md`. (`XML_TAG_PROTOCOL.md` was written in Step 0.)
Size: L.

---

## 12. Architectural Risks

### Risk 1: XML tag implicit contract (P1)

Prompts and the control plane share an implicit protocol via XML tag names.
Changing one side without the other silently fails — `extract_xml_tag()` returns
empty string, experiment aborts with a warning that may not be obvious.

**Mitigation:** Write `XML_TAG_PROTOCOL.md` in Step 0 (before any code moves).
Treat framework-stable tags as an immutable protocol. Only `artifact_xml_tag`
is configurable.

### Risk 2: State file forward/backward compatibility

Existing SSSP sessions have accumulated `idea-store.json`, `master.json`,
`exploitation-progress.json`. The extraction changes path resolution (mbmm.py fix).
If an existing session resumes after extraction, store files must still load correctly.

**Mitigation:** Version the state file format from day one. Add `"format_version"`
field to `idea-store.json` and `master.json`. Framework checks version at load and
runs migration if needed. The mbmm.py `init()` change (Step 1) must be
backwards-compatible with existing store format.

### Risk 3: Anthropic SDK coupling

`control_plane.py` imports `anthropic` directly and handles `anthropic.AuthenticationError`.
Acceptable for v1 but means the framework is Anthropic-only.

**Decision for v1:** Accept Anthropic coupling. Document it explicitly. Structure
`call_agent()` as a single function that would be the only thing to change for
a different provider. Do not abstract to an interface yet. Lazy initialization
(Step 8) is required to make the rest of the framework unit-testable.

### Risk 4: Verifier contract migration complexity (NEW)

The existing `run_verifier()` is 76 lines with Docker fallback, temp directory management,
and Python subprocess fallback. Moving this to `verify.sh` requires replicating all that
orchestration in shell, or restructuring it as a Python script called by `verify.sh`.

**Mitigation:** For the SSSP reference instance, `verify.sh` can be a thin shell wrapper
that invokes the existing `verify.py` with the right arguments and writes `verdict.json`
from its output. The framework's subprocess call then only needs to invoke `verify.sh`.
The Docker/fallback complexity stays inside `verify.sh`, hidden from the framework.
This means Step 4 is a refactor, not a rewrite.

---

## 13. What This Enables

Once extracted, the framework can be applied to:

- **Compiler flags / build systems** — optimize compilation flags for a benchmark
- **Database query planning** — optimize query plans measured by execution time
- **Neural architecture search** — optimize model architecture by validation loss
- **Numerical methods** — optimize solver parameters by convergence speed
- **Game AI** — optimize bot strategy by win rate against fixed opponents
- **Configuration tuning** — optimize any system config by any scalar metric

Requirements for a new instance:
1. A measurable artifact (the file being optimized)
2. A benchmark script that outputs `METRIC <name>=<value>` lines
3. A verifier script that writes a verdict JSON per §5.2
4. A `goal.md` describing the problem
5. A `domain_context.md` with domain knowledge for the researcher agents
6. An `instance.json` with all required fields
