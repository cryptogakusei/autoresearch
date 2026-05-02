# Autoresearch System Design

A general-purpose automated research loop that uses an LLM agent to implement experiments, diagnose results, and propose follow-up ideas — guided by deterministic scoring from experiment history.

The system separates **control** (deterministic code that decides what to try and scores results) from **intelligence** (AI that implements changes, explains outcomes, and proposes new directions).

AI interactions are side-effect-limited. The AI may edit only the explicitly allowed implementation artifact during IMPLEMENT. For all other interactions, the AI returns structured data that conforms to schemas. The deterministic controller validates those outputs and performs any authoritative state changes.

---

## Interaction and Authority Model

The AI does not directly call privileged system operations. It receives prompts and returns either:

1. edits to an allowed experiment artifact during IMPLEMENT
2. structured output conforming to an approved schema

The controller owns all side effects:

```
AI response → schema validation → controller action → persisted state
```

Structured-output schemas may be implemented using tool-calling APIs, JSON schema, function-call syntax, or another typed-response mechanism. Architecturally, they are not privileged tools. They are data contracts.

**AI may directly do:**

```
- Read allowed source/config files needed for implementation
- Modify only the current experiment artifact or allowed implementation files
- Return structured implementation summaries and element tags
- Return structured diagnoses
- Return structured candidate ideas
- Return structured candidate evaluations when EVALUATE is triggered
- Return structured seed ideas from controller-provided references
```

**AI may not directly do:**

```
- Pick the next idea
- Run, inspect, or modify the benchmark/evaluator
- Validate correctness
- Write benchmark results
- Write idea memory files
- Write historical artifacts
- Update metrics, costs, vocabulary, selections, or leaderboard state
- Recompute or overwrite CCTS signals
- Decide whether EVALUATE should run
- Add ideas to the tree
- Seed or overwrite idea memory directly
- Start, pause, resume, or stop the autoloop
- Enforce or change budget/experiment limits
- Modify guardrails, verifier files, reference outputs, or hidden tests
```

**Controller responsibilities:**

```
- Construct prompts with only allowed context
- Enforce file access and artifact boundaries
- Validate every structured AI output
- Reject malformed or policy-violating outputs
- Perform deterministic transitions
- Persist authoritative state
- Run benchmarks and validators
- Archive artifacts
- Expose only summarized benchmark results back to AI
```

---

## LLM Inference Profiles

The system may use different LLM profiles for different inference roles, but these are one-shot inference configurations, not long-running autonomous agents. The controller starts an inference, receives edits or structured output, validates it, applies allowed side effects, and the inference ends.

Inference roles:

```
- seed
- implement
- diagnose
- incremental
- divergent
- evaluate
```

Profile settings may control:

```
- model
- temperature
- max output tokens
- prompt template
- allowed context
- structured output schema
- repair retry policy
```

Profile settings may not grant authority:

```
- no profile can run benchmarks
- no profile can write idea memory directly
- no profile can mutate metrics, costs, selections, or vocabulary
- no profile can start/stop loop execution
- no profile can read hidden validator data
```

Example:

```
llmProfiles: {
  seed:        { model: "general", temperature: 0.3, maxOutputTokens: 2000 },
  implement:  { model: "code",    temperature: 0.1, maxOutputTokens: 3000 },
  diagnose:   { model: "general", temperature: 0.2, maxOutputTokens: 1000 },
  incremental:{ model: "general", temperature: 0.7, maxOutputTokens: 1200 },
  divergent:  { model: "general", temperature: 0.9, maxOutputTokens: 1600 },
  evaluate:   { model: "general", temperature: 0.2, maxOutputTokens: 1000 }
}
```

MVP default: one global profile can be used for all roles, with per-role overrides added only when useful.

### Provider Configuration

Secrets live in a local `.env` file that is never committed. Non-secret provider settings live in `.autoresearch/config.json`. Descriptor-level `llmProfiles` provide per-experiment defaults, and runtime config/env can override them.

Recommended precedence:

```
CLI flags
  > .autoresearch/config.json
  > descriptor llmProfiles
  > .env fallback values
  > built-in defaults
```

Example `.env`:

```
AUTORESEARCH_LLM_PROVIDER=openai-compatible
AUTORESEARCH_LLM_BASE_URL=https://api.openai.com/v1
AUTORESEARCH_LLM_API_KEY=sk-...
AUTORESEARCH_LLM_MODEL=gpt-4.1
```

Anthropic `.env`:

```
AUTORESEARCH_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
AUTORESEARCH_LLM_MODEL=claude-3-5-sonnet-latest
```

Example `.autoresearch/config.json`:

```
{
  "llm": {
    "provider": "openai-compatible",
    "baseUrl": "https://api.openai.com/v1",
    "apiKeyEnv": "AUTORESEARCH_LLM_API_KEY",
    "defaultModel": "gpt-4.1",
    "profiles": {
      "implement": {
        "model": "gpt-4.1",
        "temperature": 0.1,
        "maxOutputTokens": 3000
      },
      "divergent": {
        "model": "gpt-4.1",
        "temperature": 0.9,
        "maxOutputTokens": 1600
      }
    }
  }
}
```

Provider settings do not change authority. Even if a stronger model is used for IMPLEMENT or EVALUATE, the controller still enforces file boundaries, schema validation, benchmark isolation, and state ownership.

---

## The Experiment Cycle (8 Steps)

```
1. PICK           deterministic    Select next idea from tree
2. IMPLEMENT       LLM inference 1      Edit allowed artifact + return implementation schema
3. BENCHMARK       automated            Run the experiment, collect metrics
4. DIAGNOSE        LLM inference 2      Explain result + return diagnosis schema
5. UPDATE SIGNALS  deterministic        Recompute CCTS element ratios
6. GENERATE        LLM inferences 3+4   Propose incremental + divergent ideas
7. EVALUATE        conditional LLM      Resolve ambiguity when needed
8. SELECT          deterministic        Add top 3 to idea tree
```

---

## Pre-Loop Seeding: SEED FROM REFERENCE

Before the experiment loop starts, the controller can seed the idea tree from a paper, reference file, prior artifact, or user-provided source. This is a separate LLM inference from Step 6 GENERATE because there is no current experiment, parent result, or meaningful CCTS history yet.

Commands:

```
/seed                         Seed default initial ideas
/seed {type}                  Seed default initial ideas for an experiment type
/seed-from-paper {path|id}    Extract root ideas from a paper
/seed-from-file {path}        Extract root ideas from a local reference/artifact
/seed-from-ref {name}         Extract root ideas from a named configured reference
```

Optional modes:

```
--preview                     Show extracted ideas without writing idea memory
--accept-top N                Write only the top N validated seed ideas
```

**Controller flow:**

```
1. Load the requested reference or artifact
2. Sanitize/extract allowed text and metadata
3. Construct the seed prompt
4. Run SEED IDEAS LLM inference
5. Validate seed_ideas structured output
6. Deduplicate against existing root/pending ideas
7. Add accepted ideas as root pending ideas
8. Add declared elements to the vocabulary
9. Record source metadata in idea memory
```

**Prompt:**

```
Extract initial experiment ideas for {experimentType} from this reference.

Reference:
  Title/source: {source}
  Excerpt/content:
  {sanitized_reference_text}

Known elements: {vocabulary list}

Return 3-8 concrete, testable root ideas.
Each idea should have:
- a hypothesis
- a concrete implementation plan
- structural element tags
- expected impact
- source/rationale connecting it to the reference

Do not call benchmarks or evaluate whether the ideas are good.
Do not write idea memory.
Return seed_ideas structured output.
```

**Structured output: `seed_ideas`**

```
Parameters:
  ideas: Array<{
    hypothesis: string,
    plan: string,
    elements: string[],
    expectedImpact?: string,
    source: string,
    rationale: string
  }>
```

The controller validates the output and writes accepted ideas as roots:

```
{
  id: "{new_id}",
  parentId: null,
  hypothesis,
  plan,
  expectedImpact,
  experimentType,
  status: "pending",
  elements,
  source,
  children: []
}
```

SEED FROM REFERENCE is allowed to create starting points only through controller validation. It cannot mark ideas done, attach metrics, create fake benchmark results, or modify historical artifacts.

---

## Step 1: PICK (Deterministic)

**What it does:** Prioritizes all pending ideas and picks the highest-priority one.

**Priority formula:**

```
For each pending idea:
  score = 0

  If idea has a parent:
    score += parent's primary metric value × 10
    If parent diagnosis was "improved": score += 5

  If idea is a root (no parent):
    score += 3

  Depth penalty: score -= depth × 0.5
    (prefers breadth over depth)

  Age bonus: score += min(hours_pending, 5) × 0.2
    (prevents old ideas from being starved)

Pick the idea with the highest priority score.
```

**Inputs:** Idea tree from persistent storage

**Outputs:** One idea to implement next

---

## Step 2: IMPLEMENT (LLM Inference 1)

**Prompt structure:**

```
Implement experiment {id}:

**Plan:** {idea.plan}

**Known elements:** {vocabulary list}

{domain-specific implementation instructions}
Do not run benchmarks or decide whether the artifact is good.
Do not self-validate correctness or performance.
Return record_implementation structured output to summarize changes and tag structural elements.
```

**What the AI does:**
1. Reads the relevant code/config
2. Makes the changes described in the plan
3. Returns `record_implementation` structured output

The IMPLEMENT inference is not an evaluator. It should not decide whether the artifact improved, passed, failed, or is worth keeping. Those judgments belong to BENCHMARK, DIAGNOSE, UPDATE SIGNALS, PRE-EVALUATE, and SELECT.

**Structured output: `record_implementation`**

```
Parameters:
  summary: string    — 3-5 line description of what changed and why
  elements: string[] — structural tags for this experiment
```

The controller validates the output, saves the summary on the idea for use in diagnosis, and adds elements to the vocabulary for CCTS scoring.

Implementation-time checks are limited to artifact construction hygiene, such as avoiding syntax that is obviously malformed when that can be checked without invoking the benchmark or verifier. Any correctness, performance, reward, or acceptance signal must come from the deterministic control layer.

---

## Step 3: BENCHMARK (Automated — No AI)

Only the deterministic control layer may invoke the benchmark. The AI is not allowed to run the benchmark, edit the benchmark harness, inspect hidden reference data, or write benchmark results. This prevents reward hacking and keeps evaluation independent from implementation.

**What it does:**

```
1. Extract the experiment artifact (code, config, etc.)
2. Save artifact for historical record
3. Parse structural properties (domain-specific)
4. Run the experiment type's benchmark runner
5. Parse primary and secondary metrics
6. Validate correctness and acceptance constraints
7. Record results
```

Each experiment type defines its own benchmark runner, result parser, validation software, metric schema, and acceptance constraints. The primary metric is used for ranking and CCTS sorting. Secondary metrics can be recorded for diagnosis or enforced as gates.

**Safety check:** If results don't match the expected experiment type, or if required validation/secondary constraints fail, mark the experiment as failed or invalid rather than storing it as a successful completed result.

**Metric gates:**

```
Primary metric:
  - the metric optimized by PICK, leaderboard, and UPDATE SIGNALS
  - has a direction: maximize or minimize

Secondary metrics:
  - recorded for diagnosis and audit
  - may have hard constraints, such as:
    accuracy >= 0.999 on validation set
    memory_gb <= 24
    latency_p95_ms <= 10

Failed gates:
  - prevent the result from being treated as a valid improvement
  - are shown to DIAGNOSE as failure context
  - do not update bestIdPerSize as successful results
```

**Benchmark boundary:**

```
AI may:
  - modify only the allowed experiment artifact during IMPLEMENT
  - read public domain instructions and allowed reference files

AI may not:
  - invoke benchmark commands or scripts
  - modify benchmark/verifier/reference files
  - read hidden test cases or expected outputs
  - write or edit benchmark result files
  - optimize against private evaluator details

Control layer must:
  - run the benchmark from deterministic code only
  - validate result type and correctness
  - store metrics and artifacts
  - expose only summarized results needed for DIAGNOSE
```

---

## Step 4: DIAGNOSE (LLM Inference 2)

**Prompt structure:**

```
Experiment {id} results: {primary_metric_value} {unit} on {primary_size}. Diagnose.

**Implementation summary:** {idea.implementationSummary}

**Structural analysis:**
{comparison table: parent vs current structural properties}

**Results:**
  {for each size: parent_value → current_value (↑/↓ percentage)}
```

**How the prompt is constructed:**
- Header — shows primary metric using the experiment type's descriptor
- Implementation summary — from step 2's `record_implementation` structured output
- Structural analysis — deterministic comparison of parsed properties
- Results — each metric formatted with parent comparison

**What the AI does:** Returns `diagnose` structured output:

```
Parameters:
  outcome: "improved" | "regressed" | "neutral" | "error"
  reason: string — specific explanation of why
```

---

## Step 5: UPDATE SIGNALS (Deterministic)

**Algorithm: CCTS (Contrastive Concept-Tree Search)**

Runs immediately after diagnosis. Recomputes historical element signals from scratch using ALL completed experiments.

```
1. Take all completed experiments with metrics AND elements
   (need at least 4 for meaningful ratios)

2. Sort by primary metric descending

3. Split into good (top half) and bad (bottom half)

4. For each structural element:
   pGood = count(element in good) / count(good)
   pBad  = count(element in bad) / count(bad)
   ratio = pGood / pBad
   (smoothing: 0.1 when count is 0)

   ratio > 1 = element appears more in good experiments
   ratio < 1 = element appears more in bad experiments

5. For element PAIRS (only when 3+ experiments per side):
   Same computation for co-occurring element pairs
   Only stored if ratio > 3.0 or < 0.33 (strong signal)

6. Candidate scoring:
   score = geometric_mean(element ratios for all candidate's elements)
   Unknown elements score 1.0 (neutral)
```

**Example output:**

```
CCTS element signals (what historically works):
  Strong positive: technique-A (3.0×), setting-B (7.5×)
  Positive: approach-C (2.5×)
  Negative: old-method-D (0.5×)
  Avoid: baseline-E (0.05×)
  Notable pairs: technique-A+setting-B (30.0×)
```

Based on: [Contrastive Concept-Tree Search (CCTS), 2026](https://arxiv.org/abs/2602.03132)

---

## Step 6: GENERATE (LLM Inferences 3 + 4)

Two separate LLM inferences force diversity. One prompt asking for both types tends to produce all incremental ideas.

### Inference 3 — INCREMENTAL

**Prompt:**

```
Propose 1-2 ideas that are small variations of the current approach. 
Change one parameter or make a minor structural tweak.

CCTS element signals (what historically works):
  {formatted signals from step 5}

Known elements: {vocabulary list}

Tag each idea with structural elements.
Return propose_incremental structured output.
```

**Structured output: `propose_incremental`**

```
Parameters:
  ideas: Array<{
    hypothesis: string,
    plan: string,
    elements: string[]
  }>
```

### Inference 4 — DIVERGENT (queued as follow-up)

**Prompt:**

```
Propose 1-2 ideas that are fundamentally different from what we've 
tried. Not a parameter tweak — a different approach or technique entirely.

CCTS element signals (what historically works):
  {formatted signals from step 5}

Known elements: {vocabulary list}

Available references (read if helpful):
  {list of reference files}

Tag each idea with structural elements.
Add new element names for genuinely new concepts.
Return propose_divergent structured output.
```

**Structured output: `propose_divergent`**

```
Parameters:
  ideas: Array<{
    hypothesis: string,
    plan: string,
    elements: string[],
    source?: string  — reference file or knowledge source
  }>
```

Both inferences collect candidates into a shared array. When both complete, the deterministic controller runs pre-evaluation. It either selects candidates directly or triggers step 7 when semantic judgment is needed.

---

## Step 7: EVALUATE (Conditional LLM Inference)

EVALUATE is a conditional semantic arbitration step. The controller computes CCTS scores, checks cheap deterministic signals, and only runs this LLM inference when there is ambiguity worth spending tokens on.

**Deterministic pre-evaluation:**

```
1. Compute CCTS score for each candidate
2. Build a deterministic ranking
3. Remove exact duplicates only
4. Flag possible semantic duplicates
5. Detect selection ambiguity
6. Either:
   - skip AI and pass deterministic ranking to SELECT
   - run EVALUATE with trigger reasons and flagged cases
```

**Trigger reasons are produced by deterministic code**, not by AI. The AI resolves ambiguity; it does not decide whether it should be called.

Example triggers:

```
- One or more candidates may duplicate an existing pending idea
- Two generated candidates may be semantic duplicates
- Top candidates have similar CCTS scores
- A divergent candidate uses mostly new/neutral elements
- Budget is low, so selection should be more selective
- Recent experiments have stalled or regressed
```

Exact duplicates can be removed deterministically. Semantic duplicates are only flagged deterministically and resolved by AI when EVALUATE runs.

**Possible duplicate signals:**

```
- Same normalized hypothesis or plan
- Same parent, same element set, and similar plan terms
- High element overlap with an existing pending idea
- Same parameter target with a different wording
```

**Prompt:**

```
Evaluate these candidates before adding them to the idea tree.

This inference was triggered because:
{trigger_reasons}

Your job:
1. Remove semantic duplicates of existing pending ideas or other candidates.
2. Remove infeasible or unsafe candidates.
3. Preserve genuinely novel candidates when they are plausible, even if their CCTS score is neutral.
4. Rank the remaining candidates for the next idea-tree additions.

Candidates:
  A: {hypothesis}
     Plan: {plan}
     Elements: {elements}
     CCTS score: {computed score}
     Deterministic rank: {rank}

  B: {hypothesis}
     Plan: {plan}
     Elements: {elements}
     CCTS score: {computed score}
     Deterministic rank: {rank}

  C: {hypothesis}
     Plan: {plan}
     Elements: {elements}
     CCTS score: {computed score}
     Deterministic rank: {rank}

Possible duplicates flagged by deterministic checks:
  - Candidate B may duplicate pending idea 017
    Candidate: {candidate_hypothesis}
    Existing: {pending_hypothesis}
    Reason flagged: {deterministic_signal}

Existing pending ideas:
  - {id}: {hypothesis}
  - {id}: {hypothesis}

Budget remaining: {percentage}%

Note: CCTS scores for divergent ideas only reflect their known 
elements. New elements have no data and are scored neutral (1.0). 
Judge novel ideas on their reasoning and potential, not their CCTS score.

Remove semantic duplicates or infeasible ideas.
Rank the rest considering CCTS scores, trends, and novelty.
Return evaluate_candidates structured output with top 3 in order.
```

**Structured output: `evaluate_candidates`**

```
Parameters:
  ranked: string[]                             — ["C", "A", "B"] (labels in order)
  removed: Array<{label, reason, duplicateOf?}> — candidates removed with reasons
  reasoning: string                            — brief explanation
```

If no trigger fires, EVALUATE is skipped and SELECT receives the deterministic ranking directly.

**Deterministic ranking when EVALUATE is skipped:**

```
1. Rank by CCTS candidate score
2. Prefer diversity from existing pending ideas when scores are close
3. Preserve at least one plausible divergent idea when available
4. Use generation order as a final tie-breaker
```

---

## Step 8: SELECT (Deterministic)

```
1. Take the selected ranking (AI-evaluated or deterministic, max 3)
2. For each, create a new idea in the tree:
   - parentId = current experiment
   - Inherits experiment type from parent
   - Elements recorded from candidate
3. New element names added to vocabulary
4. Record selection metadata:
   - mode = "deterministic" | "ai_evaluated"
   - triggerReasons, if any
   - deterministicRanking
   - finalRanking
5. Reset candidates for next cycle
```

---

## Phase Transitions

AI response events and pipeline completion events drive transitions:

```
Pipeline completes:

  ideation pipeline completed AND currentIdea:
    → Finalize experiment (save token cost, increment counter)
    → If continuous mode: start next experiment after delay

AI finishes responding:

  phase === "diagnosing" AND currentIdea:
    → Run ideation pipeline (steps 5-8) after delay:
      - UPDATE SIGNALS
      - GENERATE incremental + divergent
      - PRE-EVALUATE
      - EVALUATE if triggered
      - SELECT

  phase === "experiment" AND currentIdea:
    → Save artifacts, parse structure
    → Trigger benchmark
    → Process results → move to diagnosis
```

---

## MVP Operational Rules

These rules make the loop implementable and resumable.

### 1. Run Recovery / Crash Resume

Recommendation: persist a small phase checkpoint in `.autoresearch/state/run.json` before and after every side-effecting controller action. On restart, the controller resumes from the last incomplete deterministic phase.

`run.json` should include:

```
{
  activeType,
  currentExperimentId,
  phase,
  phaseStatus: "not_started" | "in_progress" | "completed" | "failed",
  lastCompletedStep,
  continuousMode,
  budgetLimit,
  experimentLimit,
  updatedAt
}
```

Resume behavior:

```
implementation in_progress:
  inspect workspace/current and either resume LLM inference or mark implementation failed

artifact_archived completed, benchmark not_started:
  run benchmark from archived artifact

benchmark completed, diagnosis not_started:
  run DIAGNOSE inference from stored benchmark.json

diagnosis completed, signals not_started:
  run UPDATE SIGNALS

generation partially completed:
  keep completed candidate files and rerun only missing inference if possible

select completed, finalize not_started:
  finalize experiment and continue/pause according to run.json
```

All controller writes should be atomic: write a temp file, fsync if practical, then rename.

### 2. Failure State Model

Recommendation: keep `status` simple, but attach a structured failure record for anything failed or invalid.

```
failure: {
  phase: "seed" | "implementation" | "benchmark" | "validation" |
         "diagnosis" | "generation" | "evaluation" | "selection" |
         "persistence",
  reason: string,
  retryable: boolean,
  details?: object,
  occurredAt: string
}
```

MVP behavior:

```
retryable infrastructure failure:
  leave experiment failed with retryable=true

invalid artifact or failed validation gate:
  mark failed with retryable=false unless descriptor says otherwise

malformed LLM output after repair retry:
  mark failed with retryable=true
```

### 3. Structured Output Validation Rules

Recommendation: strict validation at controller boundaries.

Required validation:

```
- required fields must exist
- strings must be non-empty after trimming
- arrays must obey min/max counts
- element names are normalized before storage
- unknown fields are ignored for MVP
- candidate labels must be unique
- candidate references must point to known candidates
- ranked lists cannot include removed candidates
- plans must be concrete enough to implement
- outputs cannot request protected file writes or benchmark access
```

Element normalization:

```
lowercase
trim whitespace
replace spaces/underscores with hyphens
remove punctuation except hyphen
deduplicate sorted list
```

### 4. Current Workspace Reset Policy

Recommendation: each implementation starts from the parent artifact when there is a parent; root ideas start from the descriptor's baseline template.

```
pending idea has parent:
  workspace/current/{type}/ is reset from
  .autoresearch/artifacts/{type}/{parentId}/artifact/

root idea:
  workspace/current/{type}/ is reset from
  descriptor.artifact.baselinePath

failed or interrupted run:
  controller decides resume vs reset based on run.json phase
```

The AI never edits historical artifacts directly. It only edits the prepared workspace.

### 5. Reference / Source Storage

Recommendation: store controller-ingested references separately from experiment artifacts.

```
.autoresearch/
  references/
    papers/
      {referenceId}/
        source.pdf | source.txt | source.md
        metadata.json
        extracted.txt
    artifacts/
      {referenceId}/
        ...
    references.json
```

Seeded ideas should store `sourceRef` pointing to the reference id, not just free-form source text.

### 6. Descriptor Config Location

Recommendation for MVP: descriptors are code-native because benchmark parsers and structure parsers are functions.

```
src/descriptors/{experimentType}.ts
```

Each descriptor may point to external scripts:

```
benchmarks/{experimentType}/run.sh
benchmarks/{experimentType}/validate.sh
```

Later, static parts can move to JSON/YAML, but MVP should avoid splitting executable parser logic across config and code too early.

### 7. Result Parser Contract

Recommendation: benchmark runners write JSON to stdout or a known result file. The controller parser converts it into the canonical result shape.

Canonical benchmark result:

```
{
  type: "{experimentType}",
  artifactId: "{experimentId}",
  passed: true,
  metrics: {
    "{datasetOrSizeLabel}": {
      "{metricName}": number | string | boolean
    }
  },
  validation: {
    passed: boolean,
    failedConstraints: Array<{
      metric: string,
      op: string,
      expected: number | string | boolean,
      actual: number | string | boolean,
      dataset?: string,
      size?: string
    }>,
    validatorVersion?: string
  },
  metadata: {
    benchmarkVersion?: string,
    commandHash?: string,
    startedAt: string,
    completedAt: string
  }
}
```

Wrong `type`, missing primary metric, parse failure, timeout, or failed required constraint prevents the result from being treated as a valid completed experiment.

### 8. Malformed LLM Output Handling

Recommendation: one repair attempt, then fail the phase.

```
1. Validate structured output
2. If invalid, send a repair prompt containing:
   - validation errors
   - original sanitized prompt context
   - required schema
3. Validate repaired output
4. If still invalid, mark current phase failed
```

The repair prompt must not expose benchmark internals or hidden data. Repair is allowed for structured outputs only; IMPLEMENT artifact edits should not be repaired by giving benchmark feedback.

---

## Element System

### What Elements Are

Structural tags that describe an experiment's approach. Free-form strings that the AI creates and reuses.

### Lifecycle

1. **Creation:** AI declares elements in `record_implementation` structured output after implementing an experiment
2. **Vocabulary:** Extension maintains a sorted list of all known elements. Shown in prompts so the AI reuses existing names.
3. **Scoring:** CCTS computes likelihood ratios for each element — which ones correlate with good results
4. **Candidate scoring:** New ideas are scored by the geometric mean of their elements' ratios
5. **Growth:** New elements are added organically as the AI explores new approaches. Unknown elements score 1.0 (neutral) until enough data accumulates.

### Storage

Each idea has `elements?: string[]`. The vocabulary is stored in the idea tree file.

---

## Experiment Type Descriptors

Each experiment type has a descriptor that tells the system how to handle artifacts, benchmark execution, validation, metrics, prompting, and structure parsing:

```
interface ExperimentDescriptor {
  name: string;                     // experiment type identifier

  artifact: {
    baselinePath: string;           // template used for root ideas
    workingPath: string;            // mutable path AI may edit during IMPLEMENT
    allowedPaths: string[];         // file/path allowlist for implementation edits
    archiveGlobs: string[];         // files copied into immutable artifact history
  };

  benchmark: {
    command: string[];              // controller-only command/script/service runner
    timeoutSec: number;
    resultParser: string;           // parser identifier or implementation hook
  };

  validation: {
    command?: string[];             // optional controller-only validator
    hidden?: boolean;               // true if validator uses hidden/private data
    required: Array<MetricConstraint>;
  };

  metrics: {
    primary: MetricSpec;            // optimized metric used for ranking/signals
    secondary: MetricSpec[];        // recorded and optionally gated metrics
  };

  llmProfiles?: Partial<Record<
    "seed" | "implement" | "diagnose" | "incremental" | "divergent" | "evaluate",
    LlmProfile
  >>;                               // optional per-role inference overrides

  getValue(entry): number;          // extract primary metric from a result
  getIdeaValue(idea): number;       // get primary metric from an idea
  formatEntry(entry, label): string;
  formatComparison(cur, par, label): string;
  formatDiagnosisHeader(idea): string;
  formatPerfShort(idea): string;
  implementInstructions: string[];  // domain-specific implementation constraints
  parseStructure(artifact): Structure | null;
}

interface MetricSpec {
  name: string;
  unit?: string;
  direction: "maximize" | "minimize";
  primarySize?: string;             // dataset/size label used for primary ranking
  constraint?: MetricConstraint;    // optional hard gate for secondary metrics
}

interface MetricConstraint {
  metric: string;
  op: ">=" | ">" | "<=" | "<" | "==" | "!=";
  value: number | string | boolean;
  dataset?: string;
  size?: string;
}

interface LlmProfile {
  model: string;
  temperature?: number;
  maxOutputTokens?: number;
  promptTemplate?: string;
  repairRetries?: number;
}
```

Adding a new experiment type should mean adding one descriptor object plus its controller-owned benchmark/validation runner. No AI prompt should need access to private validator details.

Example metric setup:

```
metrics: {
  primary: {
    name: "tflops",
    unit: "TFLOP/s",
    direction: "maximize",
    primarySize: "n=4096"
  },
  secondary: [
    {
      name: "accuracy",
      direction: "maximize",
      constraint: {
        metric: "accuracy",
        op: ">=",
        value: 0.999,
        dataset: "validation"
      }
    },
    {
      name: "memory_gb",
      unit: "GB",
      direction: "minimize",
      constraint: {
        metric: "memory_gb",
        op: "<=",
        value: 24
      }
    }
  ]
}
```

---

## Structural Analysis

Each experiment type can define a parser that extracts structural properties from the experiment artifact. Properties are stored as a generic key-value map.

The structural analysis serves two purposes:
1. **Diagnosis context:** Shown as a comparison table (current vs parent) in the diagnosis prompt
2. **Historical record:** Saved on the idea for future reference

If an experiment type doesn't have meaningful structural properties, the parser returns null and the diagnosis prompt omits the table.

---

## Guardrails

### Deterministic Control Isolation

All deterministic steps are owned by controller code and are not exposed as free-form AI actions. The AI can provide structured outputs through approved schemas, but it cannot directly execute deterministic transitions, mutate authoritative state, or decide when protected operations run.

Deterministic-only operations:

```
- PICK: choose the next pending idea from the tree
- BENCHMARK: run evaluator, validate correctness, collect metrics
- UPDATE SIGNALS: recompute CCTS ratios from completed experiments
- PRE-EVALUATE: compute candidate scores and decide whether EVALUATE is needed
- SELECT: create new ideas from the selected ranking
- Persistence: write idea memory, metrics, selections, vocabulary, costs
- Artifact archival: save historical experiment artifacts
- Budget/limit control: enforce cost and experiment limits
- Loop control: start, pause, continue, or stop autoloop execution
- File protection: enforce writable/read-only boundaries
```

AI outputs are advisory or structured proposals until the controller accepts them. For example, `diagnose`, `propose_incremental`, `propose_divergent`, and `evaluate_candidates` return data; they do not directly edit the idea tree. `record_implementation` returns a summary and elements through a controlled schema; it does not grant access to protected storage.

### Benchmark Isolation

Benchmark execution belongs only to the deterministic control layer. The AI implements artifacts, but it does not call the benchmark or control the evaluator. The benchmark can be a shell script, compiled harness, service call, or other deterministic runner, but it must be invoked by controller code outside the AI's tool surface.

The AI receives benchmark outputs only after the controller has run validation and stored results. Prompts may include summarized metrics, comparisons, and structural analysis, but not hidden evaluator internals or private test data.

### No Implementation Self-Evaluation

The IMPLEMENT inference only creates the requested artifact. It does not verify whether the artifact is correct, performant, improved, regressed, acceptable, or worth selecting. It must not run proxy benchmarks, hidden/public validators, scoring scripts, or local tests that approximate the evaluator.

Allowed implementation hygiene:

```
- Keep the artifact within the requested interface and file boundaries
- Avoid obvious syntax/format errors when this does not invoke evaluator logic
- Explain what changed through record_implementation structured output
```

Disallowed self-evaluation:

```
- Running benchmark/verifier/test commands
- Inspecting benchmark expected outputs or hidden cases
- Comparing performance against parent/best artifacts
- Marking the experiment as passed, failed, improved, or regressed
- Editing the artifact after seeing benchmark or validator feedback
```

### File Protection

The AI is blocked from directly modifying:
- Idea memory files — only the extension writes these after validating structured AI outputs
- Historical experiment artifacts — read-only records
- Benchmark result files — written by the benchmark system, not AI
- Benchmark harness, verifier, reference outputs, and hidden tests

### Wrong-Type Safety

If benchmark results come back with a type that doesn't match the current experiment, the experiment is marked as failed instead of storing incorrect data.

### Artifact Constraints

The implement prompt tells the AI what it can and cannot change. Domain-specific constraints (e.g., "don't change the interface, only the implementation") prevent the AI from breaking the benchmark harness.

---

## Data Storage

The controller preserves experiment state and artifacts in an organized, append-only layout. AI does not write these files directly.

Recommended project layout:

```
.autoresearch/
  state/
    {experimentType}.ideas.json        # authoritative idea tree for one experiment type
    {experimentType}.signals.json      # derived CCTS element/pair signals
    run.json                           # active type, loop state, budget/limits

  artifacts/
    {experimentType}/
      {experimentId}/
        artifact/                      # archived implementation artifact(s)
          ...                          # copied from allowed implementation path
        implementation.json            # validated record_implementation output
        structure.json                 # parsed structural properties, if any
        benchmark.json                 # validated metrics and correctness result
        diagnosis.json                 # validated diagnose output
        selection.json                 # deterministic/AI-evaluated selection metadata
        prompt-context.json            # sanitized prompt inputs, optional

  logs/
    events.jsonl                       # controller events and phase transitions
    costs.jsonl                        # token/cost records by inference

workspace/
  current/
    {experimentType}/
      ...                              # mutable working artifact AI may edit during IMPLEMENT

benchmarks/
  {experimentType}/
    ...                                # read-only to AI; invoked only by controller
```

### Storage Ownership

```
AI-writable:
  workspace/current/{experimentType}/...       # only files allowed by descriptor constraints

Controller-writable:
  .autoresearch/state/...
  .autoresearch/artifacts/...
  .autoresearch/logs/...

AI-read-only or hidden:
  .autoresearch/state/...
  .autoresearch/artifacts/...
  benchmarks/{experimentType}/...
  verifier/reference/hidden test data
```

### Artifact Lifecycle

```
1. PICK chooses pending idea N
2. Controller prepares workspace/current/{experimentType}/ for N
3. IMPLEMENT LLM inference edits only allowed working artifact files
4. Controller validates record_implementation output
5. Controller copies the final working artifact into:
   .autoresearch/artifacts/{experimentType}/{experimentId}/artifact/
6. Controller parses structure and writes structure.json
7. Controller runs benchmark and writes benchmark.json
8. Controller runs DIAGNOSE inference and writes diagnosis.json
9. Controller runs UPDATE SIGNALS and updates state/{experimentType}.signals.json
10. Controller runs GENERATE/PRE-EVALUATE/EVALUATE/SELECT and writes:
    - state/{experimentType}.ideas.json
    - artifacts/{experimentType}/{experimentId}/selection.json
```

Historical artifact directories are immutable after the controller finalizes the experiment. If an experiment is retried, create a new experiment id or an explicit retry id rather than overwriting the old record.

### Idea Tree File

```
Per experiment type, a separate file:
{
  version: 1,
  nextId: N,
  ideas: {
    "001": {
      id, parentId,
      hypothesis,              — what we think will happen
      plan,                    — concrete steps to implement
      expectedImpact,          — predicted effect
      experimentType,          — which type this belongs to
      status,                  — "pending" | "running" | "done" | "failed"
      failure,                 — { phase, reason, retryable, details, occurredAt }
      created, completed,
      metrics,                 — { "label": { metric values } }
      validation,              — { passed, failedConstraints, validatorVersion }
      diagnosis,               — { outcome, reason, vsParent, vsBest }
      elements,                — ["tag-a", "tag-b", "tag-c"]
      sourceRef,               — optional reference id for seeded ideas
      implementationSummary,   — AI's description of what it changed
      structure,               — parsed structural properties
      tokenCost,               — { implementation, diagnosis, total }
      children                 — ["002", "003"]
    }
  },
  bestIdPerSize: { "primary_size": "015" },
  elementVocabulary: ["tag-a", "tag-b", "tag-c", ...],
  selections: {
    "{experiment_id}": {
      mode: "deterministic" | "ai_evaluated",
      triggerReasons: string[],
      deterministicRanking: string[],
      finalRanking: string[],
      removed?: Array<{label, reason, duplicateOf?}>,
      reasoning?: string
    }
  }
}
```

---

## Token Budget Per Experiment

```
Pre-loop SEED IDEAS        variable, only when seeding from reference

Step 2: IMPLEMENT           ~2000 tokens
Step 4: DIAGNOSE            ~800 tokens
Step 5: UPDATE SIGNALS      0 tokens (deterministic)
Step 6: GENERATE (×2)       ~1200 tokens
Step 7: EVALUATE            ~0 tokens when skipped
                            ~600 tokens when triggered
                            ─────
Typical per experiment:     ~4000 tokens without EVALUATE
                            ~4600 tokens with EVALUATE
```

---

## Commands

```
/seed              Seed initial ideas for the default type
/seed {category}   Seed ideas for a specific category
/seed-from-paper {path|id}  Extract root ideas from a paper
/seed-from-file {path}      Extract root ideas from a local reference/artifact
/seed-from-ref {name}       Extract root ideas from a named configured reference

/research          Pick next idea for active type, implement it
/research {type}   Set active type, pick and implement

/autoloop N        Run N experiments on active type
/autoloop N {type} Run N experiments targeting a specific type

/result {json}     Manually submit benchmark results

/ideas             Show idea tree for active type
/ideas {type}      Show idea tree for specific type
/best              Show leaderboard for active type
/best {type}       Show leaderboard for specific type
/cost              Show per-experiment cost breakdown

/budget $X         Set cost limit
/limit N           Set experiment count limit
/pause             Stop loop
/next              Skip current experiment
```

---

## What's Deterministic vs AI

```
DETERMINISTIC (extension code, free, instant):
  SEED acceptance   validate seed ideas and write root pending ideas
  PICK              priority formula on idea tree
  UPDATE SIGNALS    CCTS likelihood ratios from experiment data
  BENCHMARK         run evaluator and validate results
  PRE-EVALUATE      score candidates, flag ambiguity, choose whether AI is needed
  SELECT            take top N from deterministic or AI-evaluated ranking
  Persistence       authoritative writes to memory, metrics, costs, vocabulary
  Artifact archival save historical records after implementation
  Budget/loop       enforce limits and control continuous execution
  Structure parsing extract properties from experiment artifacts
  File protection   guardrails on what AI can modify
  Metric storage    result parsing and recording
  Display           tree rendering, leaderboard, status

AI (costs tokens):
  SEED IDEAS        pre-loop extraction from controller-provided references
  IMPLEMENT         make allowed artifact edits + record_implementation output
  DIAGNOSE          analyze results + diagnose output
  INCREMENTAL       propose 1-2 variations
  DIVERGENT         propose 1-2 novel ideas
  EVALUATE          conditionally resolve duplicates, feasibility, novelty, ranking

Per experiment, the loop uses 4-5 LLM inferences: IMPLEMENT, DIAGNOSE, INCREMENTAL, DIVERGENT, and optionally EVALUATE.
```

---

## Academic References

- **CCTS** (Feb 2026) — Contrastive concept-tree search with likelihood-ratio scoring. https://arxiv.org/abs/2602.03132
- **KernelBlaster** (Feb 2026) — Persistent knowledge base for optimization. https://arxiv.org/abs/2602.14293
- **EvoScientist** (Mar 2026) — Dual memory with evolution manager. https://arxiv.org/abs/2603.08127
- **AutoRefine** (Jan 2026) — Dual-form experience patterns with maintenance. https://arxiv.org/abs/2601.22758
- **SeaEvo** (Apr 2026) — Strategy-space evolution. https://arxiv.org/abs/2604.24372
- **LMABO** (Mar 2026) — LLM as acquisition function. https://arxiv.org/abs/2603.28959
- **MOOSE-Chem3** (May 2025) — Experiment-guided hypothesis ranking. https://arxiv.org/abs/2505.17873
- **BAVT** (Mar 2026) — Budget-aware tree search. https://arxiv.org/abs/2603.12634
- **AI Scientist Feedback** (Mar 2026) — Structured feedback drives learning. https://arxiv.org/abs/2603.26177
