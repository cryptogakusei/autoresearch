from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from typing import Any

from .config import load_config
from .descriptor import ExperimentDescriptor
from .llm import LlmClient, MockLlmClient, TokenUsage
from .models import Idea


def build_llm_client(
    root: Path, descriptor: ExperimentDescriptor, provider_override: str | None = None
) -> LlmClient:
    config = load_config(root)
    llm_config = config.get("llm", {})
    provider = (
        provider_override
        or os.environ.get("AUTORESEARCH_LLM_PROVIDER")
        or llm_config.get("provider")
        or "mock"
    )
    provider = provider.lower().replace("_", "-")
    if provider == "mock":
        return MockLlmClient()
    if provider in {"openai-compatible", "openai"}:
        api_key_env = llm_config.get("apiKeyEnv") or "AUTORESEARCH_LLM_API_KEY"
        api_key = os.environ.get("AUTORESEARCH_LLM_API_KEY") or os.environ.get(api_key_env)
        if provider == "openai" and not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Missing LLM API key. Set AUTORESEARCH_LLM_API_KEY or provider-specific env var in .env."
            )
        return OpenAICompatibleLlmClient(
            base_url=os.environ.get("AUTORESEARCH_LLM_BASE_URL")
            or llm_config.get("baseUrl", "https://api.openai.com/v1"),
            api_key=api_key,
            default_model=os.environ.get("AUTORESEARCH_LLM_MODEL")
            or llm_config.get("defaultModel", "gpt-4.1"),
            profiles={**descriptor.llmProfiles, **llm_config.get("profiles", {})},
        )
    if provider == "anthropic":
        api_key_env = llm_config.get("apiKeyEnv") or "ANTHROPIC_API_KEY"
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError("Missing Anthropic API key. Set ANTHROPIC_API_KEY in .env.")
        config_is_anthropic = str(llm_config.get("provider", "")).lower().replace("_", "-") == "anthropic"
        return AnthropicLlmClient(
            base_url=os.environ.get("AUTORESEARCH_LLM_BASE_URL")
            or (llm_config.get("baseUrl") if config_is_anthropic else None)
            or "https://api.anthropic.com/v1",
            api_key=api_key,
            default_model=os.environ.get("AUTORESEARCH_LLM_MODEL")
            or (llm_config.get("defaultModel") if config_is_anthropic else None)
            or "claude-3-5-sonnet-latest",
            profiles={**descriptor.llmProfiles, **llm_config.get("profiles", {})},
        )
    raise RuntimeError(f"Unsupported LLM provider: {provider}")


class PromptingLlmClient(LlmClient):
    """Base class for JSON-only prompt construction.

    This adapter intentionally returns data only. For IMPLEMENT, file changes are
    returned as proposed file contents and the controller applies them after
    allowlist validation.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        profiles: dict[str, Any] | None = None,
    ):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.profiles = profiles or {}

    def seed_ideas(
        self, descriptor: ExperimentDescriptor, source: str, text: str
    ) -> dict[str, Any]:
        prompt = f"""
Extract initial experiment ideas for {descriptor.name} from this reference.

Reference source: {source}
Reference text:
{text[:12000]}

Return JSON exactly shaped as:
{{
  "ideas": [
    {{
      "hypothesis": "...",
      "plan": "...",
      "elements": ["..."],
      "expectedImpact": "...",
      "source": "{source}",
      "rationale": "..."
    }}
  ]
}}
""".strip()
        return self._json_inference("seed", prompt)

    def implement(
        self, descriptor: ExperimentDescriptor, idea: Idea, workspace: Path
    ) -> dict[str, Any]:
        files = []
        for allowed in descriptor.artifact.allowedPaths:
            path = Path(allowed.format(type=descriptor.name, experiment_type=descriptor.name))
            if not path.is_absolute():
                path = workspace.parents[2] / path
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            files.append({"path": str(path.relative_to(workspace.parents[2])), "content": content})

        instructions = "\n".join(f"- {item}" for item in descriptor.implementInstructions)
        prompt = f"""
Implement experiment {idea.id}.

Plan:
{idea.plan}

Known elements:
{idea.elements}

Domain instructions:
{instructions}

Allowed files and current contents:
{json.dumps(files, indent=2)}

Do not run benchmarks. Do not self-validate correctness or performance.
Return the complete full contents of every changed file, not a diff or patch.
At least one allowed file must be semantically changed from the current contents.
Prefer changing the Python model file when the plan is architectural.
Return JSON exactly shaped as:
{{
  "summary": "3-5 line summary",
  "elements": ["structural-tag"],
  "files": [
    {{"path": "allowed/relative/path", "content_lines": ["line 1", "line 2"]}}
  ]
}}
Only include files from the allowed list.
Use content_lines instead of a multiline content string.
You must include at least one file entry with changed content.
""".strip()
        return self._json_inference("implement", prompt)

    def diagnose(
        self,
        descriptor: ExperimentDescriptor,
        idea: Idea,
        benchmark_result: dict[str, Any],
        parent: Idea | None,
    ) -> dict[str, Any]:
        prompt = f"""
Diagnose experiment {idea.id}.

Implementation summary:
{idea.implementationSummary}

Current result:
{json.dumps(benchmark_result, indent=2)}

Parent metrics:
{json.dumps(parent.metrics if parent else None, indent=2)}

Return JSON exactly shaped as:
{{"outcome": "improved|regressed|neutral|error", "reason": "..."}}
""".strip()
        return self._json_inference("diagnose", prompt)

    def _signal_summary(self, signals: dict[str, Any]) -> tuple[list[str], list[str]]:
        good = [e for e, r in signals.get("elements", {}).items() if r > 1.5]
        bad = [e for e, r in signals.get("elements", {}).items() if r < 0.7]
        return good, bad

    def propose_incremental(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        good, bad = self._signal_summary(signals)
        signal_lines = ""
        if good or bad:
            signal_lines = f"\nWorking: {', '.join(good[:8]) or 'unclear'}. Not working: {', '.join(bad[:5]) or 'unclear'}."
        prompt = f"""
You are proposing model architecture or hyperparameter changes for {descriptor.name}.
All ideas must be concrete changes to the neural network, loss function, or training config.
Do NOT propose changes to the experiment process, search strategy, or evaluation method.

Experiment {idea.id} tried: {idea.hypothesis}{signal_lines}

Propose 1-2 small variations — tweak architecture, hyperparameters, or loss weighting.
Return JSON: {{"ideas": [{{"hypothesis": "...", "plan": "...", "elements": ["..."]}}]}}
""".strip()
        return self._json_inference("incremental", prompt)

    def scout_queries(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        good, bad = self._signal_summary(signals)
        signal_summary = " ".join([f"+{e}" for e in good[:5]] + [f"-{e}" for e in bad[:3]])
        if not signal_summary:
            signal_summary = "no signal yet"
        prompt = f"""
Domain: {descriptor.name}. Last experiment tried: {idea.hypothesis}
Signal summary: {signal_summary}
Generate 2-3 arXiv search queries for neural network architecture or training techniques we haven't tried.
Queries must be about model design, not about experimentation methodology.
Return JSON: {{"queries": ["...", "..."]}}
""".strip()
        return self._json_inference("scout", prompt)

    def propose_divergent(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any],
        paper_context: str = "",
    ) -> dict[str, Any]:
        good, bad = self._signal_summary(signals)
        signal_lines = ""
        if good or bad:
            signal_lines = f"\nWorking: {', '.join(good[:8]) or 'unclear'}. Not working: {', '.join(bad[:5]) or 'unclear'}."
        lit_lines = ""
        if paper_context:
            lit_lines = f"\n\nRelevant literature:\n{paper_context}"
        prompt = f"""
You are proposing model architecture or training changes for {descriptor.name}.
All ideas must be concrete changes to the neural network, loss function, or training config.
Do NOT propose changes to the experiment process, search strategy, or evaluation method.

Experiment {idea.id} tried: {idea.hypothesis}{signal_lines}{lit_lines}

Propose 1-2 fundamentally different approaches. Each must differ structurally from past experiments.
If literature is provided, cite the arxiv ID in "source". Otherwise set source to "signal-driven".
Return JSON: {{"ideas": [{{"hypothesis": "...", "plan": "...", "elements": ["..."], "source": "..."}}]}}
""".strip()
        return self._json_inference("divergent", prompt)

    def evaluate_candidates(
        self,
        descriptor: ExperimentDescriptor,
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = f"""
Evaluate candidate ideas for {descriptor.name}.

Context:
{json.dumps(context, indent=2)}

Candidates:
{json.dumps(candidates, indent=2)}

Return JSON exactly shaped as:
{{"ranked": ["A"], "removed": [{{"label": "B", "reason": "...", "duplicateOf": "..."}}], "reasoning": "..."}}
""".strip()
        return self._json_inference("evaluate", prompt)

    def _json_inference(self, role: str, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def _profile(self, role: str) -> dict[str, Any]:
        raw = self.profiles.get(role, {})
        if hasattr(raw, "__dict__"):
            raw = {
                k: v
                for k, v in raw.__dict__.items()
                if v is not None and k != "promptTemplate"
            }
        if str(raw.get("model", "")).startswith("mock-"):
            raw = dict(raw)
            raw.pop("model", None)
        return raw


class OpenAICompatibleLlmClient(PromptingLlmClient):
    """Minimal OpenAI-compatible chat-completions JSON client."""

    def _json_inference(self, role: str, prompt: str) -> dict[str, Any]:
        profile = self._profile(role)
        payload: dict[str, Any] = {
            "model": profile.get("model", self.default_model),
            "messages": [
                {
                    "role": "system",
                    "content": "You return valid JSON only. Do not include markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        temp = profile.get("temperature")
        if temp is not None:
            payload["temperature"] = temp
        if profile.get("maxOutputTokens"):
            payload["max_tokens"] = profile["maxOutputTokens"]
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM provider HTTP {exc.code}: {body}") from exc
        usage = raw.get("usage", {})
        self.usage_log.append(TokenUsage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=raw.get("model", payload["model"]),
            role=role,
        ))
        content = raw["choices"][0]["message"]["content"]
        return _loads_json_content(content)


class AnthropicLlmClient(PromptingLlmClient):
    """Minimal Anthropic Messages API JSON client."""

    def _json_inference(self, role: str, prompt: str) -> dict[str, Any]:
        profile = self._profile(role)
        payload = {
            "model": profile.get("model", self.default_model),
            "max_tokens": profile.get(
                "maxOutputTokens", 8000 if role == "implement" else 2000
            ),
            "temperature": profile.get("temperature", 0.2),
            "system": "You return valid JSON only. Do not include markdown.",
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": "return_json",
                    "description": "Return the structured JSON object requested by the user.",
                    "input_schema": _anthropic_schema_for_role(role),
                }
            ],
            "tool_choice": {"type": "tool", "name": "return_json"},
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic provider HTTP {exc.code}: {body}") from exc
        usage = raw.get("usage", {})
        self.usage_log.append(TokenUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            model=raw.get("model", payload["model"]),
            role=role,
        ))
        content_blocks = raw.get("content", [])
        for block in content_blocks:
            if block.get("type") == "tool_use" and block.get("name") == "return_json":
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    return tool_input
        text = "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")
        if not text.strip():
            raise RuntimeError(f"Anthropic returned no text content: {raw}")
        return _loads_json_content(text)


def _loads_json_content(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _anthropic_schema_for_role(role: str) -> dict[str, Any]:
    if role == "implement":
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "elements": {"type": "array", "items": {"type": "string"}},
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content_lines": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["path", "content_lines"],
                    },
                },
            },
            "required": ["summary", "elements", "files"],
            "additionalProperties": False,
        }
    if role == "seed":
        return {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "hypothesis": {"type": "string"},
                            "plan": {"type": "string"},
                            "elements": {"type": "array", "items": {"type": "string"}},
                            "expectedImpact": {"type": "string"},
                            "source": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["hypothesis", "plan", "elements", "source", "rationale"],
                    },
                }
            },
            "required": ["ideas"],
            "additionalProperties": False,
        }
    if role == "diagnose":
        return {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["improved", "regressed", "neutral", "error"],
                },
                "reason": {"type": "string"},
            },
            "required": ["outcome", "reason"],
            "additionalProperties": False,
        }
    if role in {"incremental", "divergent"}:
        properties: dict[str, Any] = {
            "hypothesis": {"type": "string"},
            "plan": {"type": "string"},
            "elements": {"type": "array", "items": {"type": "string"}},
        }
        if role == "divergent":
            properties["source"] = {"type": "string"}
        return {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": properties,
                        "required": ["hypothesis", "plan", "elements"],
                    },
                }
            },
            "required": ["ideas"],
            "additionalProperties": False,
        }
    if role == "evaluate":
        return {
            "type": "object",
            "properties": {
                "ranked": {"type": "array", "items": {"type": "string"}},
                "removed": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "reason": {"type": "string"},
                            "duplicateOf": {"type": "string"},
                        },
                        "required": ["label", "reason"],
                    },
                },
                "reasoning": {"type": "string"},
            },
            "required": ["ranked", "removed", "reasoning"],
            "additionalProperties": False,
        }
    if role == "scout":
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {"type": "string"},
                }
            },
            "required": ["queries"],
            "additionalProperties": False,
        }
    return {"type": "object", "additionalProperties": True}
