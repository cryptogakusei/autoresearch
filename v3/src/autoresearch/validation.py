from __future__ import annotations

from typing import Any

from .descriptor import ExperimentDescriptor, MetricConstraint


def validate_result(
    result: dict[str, Any], descriptor: ExperimentDescriptor, experiment_id: str
) -> tuple[bool, list[dict[str, Any]], str | None]:
    if result.get("type") != descriptor.name:
        return False, [{"reason": "wrong result type", "actual": result.get("type")}], None
    if result.get("artifactId") != experiment_id:
        return False, [{"reason": "wrong artifact id", "actual": result.get("artifactId")}], None
    if "metrics" not in result or not isinstance(result["metrics"], dict):
        return False, [{"reason": "missing metrics"}], None

    primary = descriptor.primary_metric()
    primary_label = primary.primarySize or next(iter(result["metrics"]), None)
    if not primary_label or primary_label not in result["metrics"]:
        return False, [{"reason": "missing primary size", "size": primary_label}], None
    if primary.name not in result["metrics"][primary_label]:
        return False, [{"reason": "missing primary metric", "metric": primary.name}], None

    failures: list[dict[str, Any]] = []
    for constraint in descriptor.validation.required:
        ok, failure = _check_constraint(result["metrics"], constraint)
        if not ok and failure:
            failures.append(failure)
    for spec in descriptor.secondary_metrics():
        if spec.constraint:
            ok, failure = _check_constraint(result["metrics"], spec.constraint)
            if not ok and failure:
                failures.append(failure)

    validator_version = (
        result.get("validation", {}).get("validatorVersion")
        if isinstance(result.get("validation"), dict)
        else None
    )
    return not failures, failures, validator_version


def _check_constraint(
    metrics: dict[str, dict[str, Any]], constraint: MetricConstraint
) -> tuple[bool, dict[str, Any] | None]:
    labels = [constraint.dataset or constraint.size] if (constraint.dataset or constraint.size) else list(metrics)
    for label in labels:
        if not label or label not in metrics or constraint.metric not in metrics[label]:
            return False, {
                "metric": constraint.metric,
                "op": constraint.op,
                "expected": constraint.value,
                "actual": None,
                "dataset": constraint.dataset,
                "size": constraint.size,
                "reason": "missing metric",
            }
        actual = metrics[label][constraint.metric]
        if not _compare(actual, constraint.op, constraint.value):
            return False, {
                "metric": constraint.metric,
                "op": constraint.op,
                "expected": constraint.value,
                "actual": actual,
                "dataset": constraint.dataset,
                "size": constraint.size,
            }
    return True, None


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == ">=":
        return actual >= expected
    if op == ">":
        return actual > expected
    if op == "<=":
        return actual <= expected
    if op == "<":
        return actual < expected
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    raise ValueError(f"Unsupported operator: {op}")

