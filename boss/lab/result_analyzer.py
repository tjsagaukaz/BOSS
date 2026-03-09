from __future__ import annotations

from typing import Any


class ResultAnalyzer:
    def analyze(
        self,
        *,
        primary_metric: str | None,
        metric_direction: str,
        variants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline = next((item for item in variants if item.get("kind") == "baseline"), None)
        candidates = [item for item in variants if item.get("kind") == "candidate" and item.get("status") == "passed"]
        metric_name = primary_metric or "runtime_seconds"
        direction = "maximize" if metric_direction == "maximize" else "minimize"

        if not candidates:
            return {
                "recommended_variant_id": None,
                "primary_metric": metric_name,
                "metric_direction": direction,
                "reason": "No candidate variant completed successfully.",
                "confidence": 0.0,
            }

        comparable = [item for item in candidates if self._metric_value(item, metric_name) is not None]
        if not comparable:
            return {
                "recommended_variant_id": None,
                "primary_metric": metric_name,
                "metric_direction": direction,
                "reason": f"No candidate exposed comparable metric '{metric_name}'.",
                "confidence": 0.0,
            }

        selector = min if direction == "minimize" else max
        chosen = selector(comparable, key=lambda item: self._metric_value(item, metric_name))
        chosen_value = self._metric_value(chosen, metric_name)
        baseline_value = self._metric_value(baseline, metric_name) if baseline is not None else None

        if baseline_value is None:
            return {
                "recommended_variant_id": chosen["variant_id"],
                "primary_metric": metric_name,
                "metric_direction": direction,
                "baseline_value": None,
                "recommended_value": chosen_value,
                "reason": f"Baseline metric '{metric_name}' was unavailable; selected the best completed variant by measured {metric_name}.",
                "confidence": 0.5,
            }

        improved = chosen_value < baseline_value if direction == "minimize" else chosen_value > baseline_value
        if not improved:
            return {
                "recommended_variant_id": None,
                "primary_metric": metric_name,
                "metric_direction": direction,
                "baseline_value": baseline_value,
                "recommended_value": chosen_value,
                "reason": f"Baseline remains best on measured {metric_name}.",
                "confidence": 0.35,
            }

        improvement_percent = self._improvement_percent(
            baseline_value=baseline_value,
            candidate_value=chosen_value,
            direction=direction,
        )
        confidence = min(0.95, 0.55 + (abs(improvement_percent) / 100.0))
        return {
            "recommended_variant_id": chosen["variant_id"],
            "primary_metric": metric_name,
            "metric_direction": direction,
            "baseline_value": baseline_value,
            "recommended_value": chosen_value,
            "improvement_percent": improvement_percent,
            "reason": f"{chosen['variant_id']} improved {metric_name} from {baseline_value:.4f} to {chosen_value:.4f}.",
            "confidence": round(confidence, 2),
        }

    def _metric_value(self, variant: dict[str, Any] | None, metric_name: str) -> float | None:
        if not variant:
            return None
        metrics = variant.get("metrics", {})
        if isinstance(metrics, dict) and metric_name in metrics:
            value = metrics[metric_name]
            if isinstance(value, (int, float)):
                return float(value)
        runtime = variant.get("runtime_seconds")
        if metric_name == "runtime_seconds" and isinstance(runtime, (int, float)):
            return float(runtime)
        return None

    def _improvement_percent(self, baseline_value: float, candidate_value: float, direction: str) -> float:
        if baseline_value == 0:
            return 0.0
        if direction == "minimize":
            return ((baseline_value - candidate_value) / abs(baseline_value)) * 100.0
        return ((candidate_value - baseline_value) / abs(baseline_value)) * 100.0
