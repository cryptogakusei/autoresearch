# XML Tag Protocol

Every XML tag used between the LLM agents and the control plane is listed here.
This document is the authoritative contract. Changing a tag name on either side
(prompt or control plane) without updating the other silently breaks the system —
`extract_xml_tag()` returns empty string, and the experiment aborts or silently
no-ops without an obvious error.

**Do not change framework-stable tags.** They are an immutable protocol.
Only `<{{ARTIFACT_XML_TAG}}>` is instance-configurable.

---

## Framework-stable tags

These tags never change. They are defined by the framework and must appear
verbatim in agent prompts. The control plane always parses them by their
exact literal names.

### `<idea_signals>` — MBMM signal array

| Property | Value |
|----------|-------|
| **Produced by** | Attribution Agent (`attribution.md`) |
| **Consumed by** | `call_attribution()` → `mbmm.apply_signals()` |
| **Content format** | JSON array of signal objects: `[{"idea_id": str, "signal": "positive"\|"negative"\|"neutral", "weight": float}]` |
| **On empty** | Output `[]` (not `NONE`) |
| **Parse failure** | `[WARN] Attribution returned invalid JSON for <idea_signals>` — signals are dropped, experiment continues |

### `<new_idea>` — single new idea entry

| Property | Value |
|----------|-------|
| **Produced by** | Attribution Agent (`attribution.md`) |
| **Consumed by** | `call_attribution()` → `mbmm.apply_signals()` |
| **Content format** | Single JSON object matching `idea-store.schema.json` |
| **On empty** | Output `NONE` (literal string) |
| **Parse failure** | `[WARN] Attribution returned invalid JSON for <new_idea>` — idea is dropped |

### `<do_not_repeat_entry>` — do-not-repeat ledger entry

| Property | Value |
|----------|-------|
| **Produced by** | Attribution Agent (`attribution.md`) |
| **Consumed by** | `call_attribution()` → `guarded_append(DO_NOT_REPEAT_MD, ...)` |
| **Content format** | Free-text prose block describing the failed approach |
| **On empty** | Output `NONE` (literal string) |
| **Side effect** | Appended verbatim to `do-not-repeat.md` in instance directory |

### `<selected_idea>` — exploration plan

| Property | Value |
|----------|-------|
| **Produced by** | Exploration Agent (`exploration.md`), phase=plan |
| **Consumed by** | `run_exploration()` — stored as `exploration_plan_xml`, injected back into phase=report prompt |
| **Content format** | XML subtree (not JSON) — contains `<id>`, `<title>`, `<hypothesis>`, `<runs>` child elements |
| **On empty** | `[WARN] Exploration agent returned no <selected_idea> — skipping`; entire exploration cycle is skipped |
| **Re-use** | The raw XML content is wrapped as `<selected_idea>...</selected_idea>` and injected as `{{EXPLORATION_PLAN}}` in the report phase |

### `<search_queries>` — literature scout query list

| Property | Value |
|----------|-------|
| **Produced by** | Literature Scout (`literature_scout.md`), phase=queries |
| **Consumed by** | `run_literature_scout()` — iterates `<query>` children with `re.finditer` |
| **Content format** | XML wrapper containing one or more `<query>text</query>` child elements |
| **On empty** | Control plane falls back to `config["fallback_queries"]` from instance.json |
| **Child tag** | `<query>` is a child of `<search_queries>`, not a top-level tag |

### `<new_references>` — literature entries to add

| Property | Value |
|----------|-------|
| **Produced by** | Literature Scout (`literature_scout.md`), phase=synthesis |
| **Consumed by** | `run_literature_scout()` → `guarded_append(REFERENCES_MD, ...)` |
| **Content format** | Markdown-formatted reference entries |
| **On empty** | Agent outputs `<no_new_references/>` (self-closing); control plane checks `if not new_refs` and skips |

### `<new_ideas>` — idea batch from scout

| Property | Value |
|----------|-------|
| **Produced by** | Literature Scout (`literature_scout.md`), phase=synthesis |
| **Consumed by** | `run_literature_scout()` → `mbmm.apply_signals()` |
| **Content format** | JSON array of idea objects matching `idea-store.schema.json` |
| **On empty** | Agent outputs `<new_ideas>[]</new_ideas>`; control plane skips if empty array |
| **Parse failure** | `[WARN] Scout returned invalid JSON for <new_ideas>: {e}` — ideas are dropped |

### `<params_json>` — updated params.json content

| Property | Value |
|----------|-------|
| **Produced by** | Implementation Agent (`implementation.md`) |
| **Consumed by** | `run_implementation()` → `guarded_write(PARAMS_JSON, ...)` |
| **Content format** | Raw JSON (the full params.json contents) |
| **On empty** | `if new_params:` guard — skipped silently; existing params.json is not modified |
| **Note** | This tag name is framework-stable because params.json is always the parameter file, regardless of instance. Contrast with the artifact tag below. |

---

## Instance-configurable tag

This tag name is set by `artifact_xml_tag` in `instance.json`. The framework
substitutes `{{ARTIFACT_XML_TAG}}` into templates before sending to the agent.

### `<{{ARTIFACT_XML_TAG}}>` — artifact file content

| Property | Value |
|----------|-------|
| **Produced by** | Implementation Agent (`implementation.md`) |
| **Consumed by** | `run_implementation()` → `extract_xml_tag(response, config["artifact_xml_tag"])` → `guarded_write(ARTIFACT_PATH, ...)` |
| **Content format** | Raw source code (or other artifact content) — written verbatim to the artifact file |
| **On empty** | `[WARN] Implementation agent returned no <artifact_xml_tag> block — aborting experiment` |
| **SSSP instance** | `artifact_xml_tag = "sssp_cpp"` → tag is `<sssp_cpp>` |
| **Constraint** | Must be a valid XML tag name (no spaces, starts with letter or `_`). Validate in `validate_instance_config()`. |

---

## Tag name rules

1. **Framework-stable tags** listed above are immutable. Never rename them.
2. **`artifact_xml_tag`** is the only configurable tag. It is set once in `instance.json`
   and substituted into framework templates at render time.
3. `extract_xml_tag(text, tag)` returns the first match of `<tag>...</tag>` (or empty string).
   Tags are case-sensitive. `<Idea_Signals>` will not match `idea_signals`.
4. Nested XML is supported for `<selected_idea>` (it has child elements). For all other
   tags, the content is treated as a flat string (JSON or prose), not parsed as XML.
5. When an agent outputs `NONE` for an optional tag, the control plane checks
   `if raw.strip().upper() != "NONE"` before parsing. Prompts must instruct agents to
   output exactly `NONE` (uppercase) — not `none`, `null`, or an empty tag.

---

## Adding a new tag

1. Add a row to this document under the appropriate section.
2. Update the agent prompt to output the tag.
3. Update `control_plane.py` to call `extract_xml_tag(response, "new_tag_name")`.
4. Add a test to `tests/test_xml_tags.py` verifying round-trip behavior.
5. If the tag carries structured data, add its schema to `schema/`.
