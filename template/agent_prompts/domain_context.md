## Domain: Your Problem Name

Replace this file with domain-specific knowledge that the research agents should know.

### What you are optimizing
Describe what {{ARTIFACT_NAME}} does and why {{METRIC_NAME}} is the right measure of quality ({{METRIC_DIRECTION}}).

### Benchmark structure
{{BENCHMARK_DESCRIPTION}}

Include any structural properties of your problem that are critical for correctness or performance reasoning.

### Known idea categories
List the categories of changes that agents should consider. For example:
- Algorithm variants
- Data structure choices
- Hyperparameter tuning
- Code-level optimizations

### Constraints that must hold
List any constraints that limit what changes are valid. For example:
- Correctness: all outputs must match reference implementation exactly
- Memory: must fit in N GB
- Compatibility: must run on X hardware

### Key prior work
Papers, blog posts, or benchmarks that the agents should know about.

---

**Important:** Do NOT hardcode any value that is already in instance.json.
Use `{{METRIC_NAME}}`, `{{ARTIFACT_NAME}}`, `{{BENCHMARK_DESCRIPTION}}`, etc.
Only add content here that has no corresponding instance.json field.
