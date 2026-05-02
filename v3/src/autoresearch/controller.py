from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil
from typing import Any

from .arxiv import fetch_papers, format_paper_context
from .benchmark import BenchmarkError, run_benchmark
from .descriptor import ExperimentDescriptor
from .llm import LlmClient
from .models import Diagnosis, Failure, Idea, SelectionRecord, ValidationRecord
from .signals import score_candidate, update_signals
from .storage import Storage
from .utils import normalize_elements, now_iso
from .validation import validate_result


class Controller:
    def __init__(self, root: Path, descriptor: ExperimentDescriptor, llm: LlmClient):
        self.root = root
        self.descriptor = descriptor
        self.llm = llm
        self.storage = Storage(root)
        self.storage.init()

    def seed_default(self) -> list[Idea]:
        source = "default"
        text = "Default seed idea for the experiment type."
        return self.seed_from_text(source, text)

    def seed_from_text(self, source: str, text: str, accept_top: int | None = None) -> list[Idea]:
        raw = self.llm.seed_ideas(self.descriptor, source, text)
        ideas = self._validate_seed_ideas(raw)
        if accept_top is not None:
            ideas = ideas[:accept_top]
        tree = self.storage.load_tree(self.descriptor.name)
        created: list[Idea] = []
        for raw_idea in ideas:
            idea_id = tree.allocate_id()
            idea = Idea(
                id=idea_id,
                parentId=None,
                hypothesis=raw_idea["hypothesis"],
                plan=raw_idea["plan"],
                expectedImpact=raw_idea.get("expectedImpact"),
                experimentType=self.descriptor.name,
                status="pending",
                elements=normalize_elements(raw_idea["elements"]),
                sourceRef=source,
                created=now_iso(),
            )
            tree.ideas[idea_id] = idea
            created.append(idea)
        self._merge_vocabulary(tree)
        self.storage.save_tree(self.descriptor.name, tree)
        self.storage.append_log("events.jsonl", {"event": "seed", "count": len(created)})
        return created

    def pick(self) -> Idea | None:
        tree = self.storage.load_tree(self.descriptor.name)
        return self._pick_from_tree(tree)

    def _pick_from_tree(self, tree: Any) -> Idea | None:
        pending = [idea for idea in tree.ideas.values() if idea.status == "pending"]
        if not pending:
            return None
        return max(pending, key=lambda idea: self._priority(tree, idea))

    def run_one(self, idea_id: str | None = None) -> Idea | None:
        tree = self.storage.load_tree(self.descriptor.name)
        if idea_id:
            idea = tree.ideas.get(idea_id)
            if idea is None:
                raise RuntimeError(f"Unknown idea id for {self.descriptor.name}: {idea_id}")
            if idea.status != "pending":
                raise RuntimeError(f"Idea {idea_id} is {idea.status}, not pending")
        else:
            idea = self._pick_from_tree(tree)
        if not idea:
            return None
        idea.status = "running"
        self.storage.save_tree(self.descriptor.name, tree)
        state = self.storage.load_run_state()
        state.activeType = self.descriptor.name
        state.currentExperimentId = idea.id
        state.phase = "implementation"
        state.phaseStatus = "in_progress"
        self.storage.save_run_state(state)

        try:
            self._prepare_workspace(tree, idea)
            workspace = self.storage.workspace_for(self.descriptor.name)
            implementation = self.llm.implement(self.descriptor, idea, workspace)
            self.storage.write_experiment_json(
                self.descriptor.name, idea.id, "implementation_attempt.json", implementation
            )
            applied_files = self._apply_implementation_files(implementation)
            if not self.llm.applies_file_edits and not applied_files:
                returned_files = implementation.get("files")
                file_count = len(returned_files) if isinstance(returned_files, list) else 0
                raise ValueError(
                    "provider-backed implementation did not return changed files "
                    f"(returned file entries: {file_count})"
                )
            implementation["filesApplied"] = applied_files
            implementation = self._validate_implementation(implementation)
            idea.implementationSummary = implementation["summary"]
            idea.elements = normalize_elements(implementation["elements"])
            self.storage.write_experiment_json(
                self.descriptor.name, idea.id, "implementation.json", implementation
            )

            artifact_dir = self.storage.archive_artifact(
                self.descriptor.name, idea.id, self.descriptor.artifact.archiveGlobs
            )
            structure = self.descriptor.parseStructure(artifact_dir)
            idea.structure = structure
            self.storage.write_experiment_json(
                self.descriptor.name, idea.id, "structure.json", structure or {}
            )

            state.phase = "benchmark"
            self.storage.save_run_state(state)
            result = run_benchmark(self.descriptor, artifact_dir, idea.id, self.root)
            passed, failures, validator_version = validate_result(
                result, self.descriptor, idea.id
            )
            validation = ValidationRecord(
                passed=passed,
                failedConstraints=failures,
                validatorVersion=validator_version,
            )
            result["validation"] = asdict(validation)
            self.storage.write_experiment_json(
                self.descriptor.name, idea.id, "benchmark.json", result
            )
            idea.metrics = result["metrics"]
            idea.validation = validation
            if not passed:
                self._fail(idea, "validation", "Validation constraints failed", retryable=False, details={"failures": failures})
                self.storage.save_tree(self.descriptor.name, tree)
                return idea

            parent = tree.ideas.get(idea.parentId) if idea.parentId else None
            diagnosis_raw = self.llm.diagnose(self.descriptor, idea, result, parent)
            diagnosis = self._validate_diagnosis(diagnosis_raw)
            idea.diagnosis = Diagnosis(**diagnosis)
            self.storage.write_experiment_json(
                self.descriptor.name, idea.id, "diagnosis.json", diagnosis
            )

            idea.status = "done"
            idea.completed = now_iso()
            self._update_best(tree, idea)
            self._merge_vocabulary(tree)
            signals = update_signals(tree, self.descriptor)
            self.storage.save_signals(self.descriptor.name, signals)
            self._generate_and_select(tree, idea, signals)
            self.storage.save_tree(self.descriptor.name, tree)

            state.phase = "finalize"
            state.phaseStatus = "completed"
            state.lastCompletedStep = "select"
            self.storage.save_run_state(state)
            self.storage.append_log("events.jsonl", {"event": "experiment_done", "id": idea.id})
            return idea
        except (BenchmarkError, Exception) as exc:
            self._fail(idea, state.phase or "unknown", str(exc), retryable=True)
            self.storage.save_tree(self.descriptor.name, tree)
            self.storage.append_log(
                "events.jsonl", {"event": "experiment_failed", "id": idea.id, "reason": str(exc)}
            )
            return idea

    def autoloop(self, count: int) -> list[Idea]:
        completed: list[Idea] = []
        for _ in range(count):
            result = self.run_one()
            if result is None:
                break
            completed.append(result)
        return completed

    def ideas(self) -> list[Idea]:
        tree = self.storage.load_tree(self.descriptor.name)
        return sorted(tree.ideas.values(), key=lambda idea: idea.id)

    def best(self) -> list[Idea]:
        tree = self.storage.load_tree(self.descriptor.name)
        done = [idea for idea in tree.ideas.values() if idea.status == "done" and idea.metrics]
        return sorted(
            done,
            key=lambda idea: self.descriptor.sort_key(self.descriptor.get_idea_value(idea)),
            reverse=True,
        )

    def _prepare_workspace(self, tree: Any, idea: Idea) -> None:
        if idea.parentId:
            source = self.storage.experiment_dir(self.descriptor.name, idea.parentId) / "artifact"
            nested_source = source / self.descriptor.artifact.workingPath
            if nested_source.exists():
                source = nested_source
        else:
            source = self.root / self.descriptor.artifact.baselinePath
        if not source.exists():
            raise RuntimeError(f"Missing workspace source: {source}")
        self.storage.reset_workspace_from(self.descriptor.name, source)

    def _priority(self, tree: Any, idea: Idea) -> float:
        score = 0.0
        if idea.parentId and idea.parentId in tree.ideas:
            parent = tree.ideas[idea.parentId]
            if parent.metrics:
                parent_value = self.descriptor.get_idea_value(parent)
                if self.descriptor.primary_metric().direction == "minimize":
                    score += (1 / (1 + max(parent_value, 0))) * 10
                else:
                    score += parent_value * 10
            if parent.diagnosis and parent.diagnosis.outcome == "improved":
                score += 5
        else:
            score += 3
        score -= self._depth(tree, idea) * 0.5
        return score

    def _depth(self, tree: Any, idea: Idea) -> int:
        depth = 0
        cur = idea
        while cur.parentId and cur.parentId in tree.ideas:
            depth += 1
            cur = tree.ideas[cur.parentId]
        return depth

    def _scout_literature(self, idea: Idea, signals: dict[str, Any]) -> str:
        try:
            raw = self.llm.scout_queries(self.descriptor, idea, signals)
            queries = raw.get("queries", [])
            if not isinstance(queries, list) or not queries:
                return ""
            queries = [q for q in queries[:4] if isinstance(q, str) and q.strip()]
            if not queries:
                return ""
            papers = fetch_papers(queries, max_per_query=3)
            if not papers:
                return ""
            context = format_paper_context(papers, max_chars=4000)
            self.storage.append_log("events.jsonl", {
                "event": "literature_scout",
                "idea": idea.id,
                "queries": queries,
                "papers_found": len(papers),
            })
            return context
        except Exception:
            return ""

    def _generate_and_select(self, tree: Any, idea: Idea, signals: dict[str, Any]) -> None:
        inc = self._validate_candidates(self.llm.propose_incremental(self.descriptor, idea, signals))
        paper_context = self._scout_literature(idea, signals)
        div = self._validate_candidates(
            self.llm.propose_divergent(self.descriptor, idea, signals, paper_context=paper_context)
        )
        candidates: list[dict[str, Any]] = []
        for idx, candidate in enumerate([*inc, *div]):
            label = chr(ord("A") + idx)
            elements = normalize_elements(candidate["elements"])
            candidates.append(
                {
                    **candidate,
                    "label": label,
                    "elements": elements,
                    "cctsScore": score_candidate(elements, signals),
                }
            )
        deterministic = sorted(candidates, key=lambda c: c["cctsScore"], reverse=True)
        final = deterministic[:3]
        selection = SelectionRecord(
            mode="deterministic",
            triggerReasons=[],
            deterministicRanking=[c["label"] for c in deterministic],
            finalRanking=[c["label"] for c in final],
        )
        for candidate in final:
            child_id = tree.allocate_id()
            child = Idea(
                id=child_id,
                parentId=idea.id,
                hypothesis=candidate["hypothesis"],
                plan=candidate["plan"],
                experimentType=self.descriptor.name,
                status="pending",
                elements=candidate["elements"],
                expectedImpact=candidate.get("expectedImpact"),
                sourceRef=candidate.get("source"),
                created=now_iso(),
            )
            tree.ideas[child_id] = child
            idea.children.append(child_id)
        tree.selections[idea.id] = selection
        self.storage.write_experiment_json(
            self.descriptor.name, idea.id, "selection.json", asdict(selection)
        )
        self._merge_vocabulary(tree)

    def _update_best(self, tree: Any, idea: Idea) -> None:
        primary = self.descriptor.primary_metric()
        label = primary.primarySize or next(iter(idea.metrics or {}), "primary")
        current_best_id = tree.bestIdPerSize.get(label)
        if not current_best_id:
            tree.bestIdPerSize[label] = idea.id
            return
        current = self.descriptor.get_idea_value(idea)
        previous = self.descriptor.get_idea_value(tree.ideas[current_best_id])
        if self.descriptor.is_better(current, previous):
            tree.bestIdPerSize[label] = idea.id

    def _merge_vocabulary(self, tree: Any) -> None:
        vocab = set(tree.elementVocabulary)
        for idea in tree.ideas.values():
            vocab.update(idea.elements)
        tree.elementVocabulary = sorted(vocab)

    def _fail(
        self,
        idea: Idea,
        phase: str,
        reason: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        idea.status = "failed"
        idea.completed = now_iso()
        idea.failure = Failure(
            phase=phase,
            reason=reason,
            retryable=retryable,
            details=details,
            occurredAt=now_iso(),
        )

    def _validate_seed_ideas(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        ideas = raw.get("ideas")
        if not isinstance(ideas, list) or not ideas:
            raise ValueError("seed_ideas must include a non-empty ideas array")
        validated = []
        for item in ideas[:8]:
            self._require_strings(item, ["hypothesis", "plan", "source", "rationale"])
            if not isinstance(item.get("elements"), list):
                raise ValueError("seed idea elements must be an array")
            validated.append(item)
        return validated

    def _validate_implementation(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._require_strings(raw, ["summary"])
        if not isinstance(raw.get("elements"), list):
            raise ValueError("record_implementation elements must be an array")
        raw["elements"] = normalize_elements(raw["elements"])
        raw.pop("files", None)
        return raw

    def _validate_diagnosis(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._require_strings(raw, ["outcome", "reason"])
        if raw["outcome"] not in {"improved", "regressed", "neutral", "error"}:
            raise ValueError("invalid diagnosis outcome")
        return {"outcome": raw["outcome"], "reason": raw["reason"]}

    def _validate_candidates(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        ideas = raw.get("ideas")
        if not isinstance(ideas, list):
            raise ValueError("candidate output must include ideas array")
        out = []
        for item in ideas[:2]:
            self._require_strings(item, ["hypothesis", "plan"])
            if not isinstance(item.get("elements"), list):
                raise ValueError("candidate elements must be an array")
            out.append(item)
        return out

    @staticmethod
    def _require_strings(data: dict[str, Any], keys: list[str]) -> None:
        for key in keys:
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"missing non-empty field: {key}")

    def _apply_implementation_files(self, raw: dict[str, Any]) -> list[str]:
        files = raw.get("files")
        if files is None:
            return []
        if not isinstance(files, list):
            raise ValueError("implementation files must be an array")
        allowed = {self._resolve_allowed_path(path) for path in self.descriptor.artifact.allowedPaths}
        applied: list[str] = []
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("implementation file entry must be an object")
            rel_path = item.get("path")
            content = item.get("content")
            content_lines = item.get("content_lines")
            if not isinstance(rel_path, str):
                raise ValueError("implementation file entries require path")
            if content is None and content_lines is not None:
                if not isinstance(content_lines, list) or not all(
                    isinstance(line, str) for line in content_lines
                ):
                    raise ValueError("content_lines must be an array of strings")
                content = "\n".join(content_lines) + "\n"
            if not isinstance(content, str):
                raise ValueError("implementation file entries require content or content_lines")
            target = self._resolve_allowed_path(rel_path)
            if target not in allowed:
                matched = [a for a in allowed if a.name == target.name]
                if len(matched) == 1:
                    target = matched[0]
                else:
                    raise ValueError(f"implementation attempted to write non-allowed path: {rel_path}")
            existing = target.read_text(encoding="utf-8") if target.exists() else None
            if existing == content:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            applied.append(str(target.relative_to(self.root)))
        return applied

    def _resolve_allowed_path(self, path: str) -> Path:
        formatted = path.format(type=self.descriptor.name, experiment_type=self.descriptor.name)
        target = Path(formatted)
        if not target.is_absolute():
            target = self.root / target
        return target.resolve()
