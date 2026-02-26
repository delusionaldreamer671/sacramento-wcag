"""Pipeline stage contracts for the WCAG remediation pipeline.

Every pipeline stage MUST return a StageResult. The orchestrator
(convert_pdf_sync) inspects StageResult.status to decide whether to
continue, degrade, or abort the pipeline.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


StageStatus = Literal["success", "skipped", "degraded", "failed"]
StageCategory = Literal["required", "required_with_fallback", "optional"]


@dataclass
class StageResult:
    """Uniform return type for every pipeline stage."""
    stage_name: str
    status: StageStatus
    data: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "degraded")


@dataclass
class StageSpec:
    """Declares a stage's name and failure policy."""
    name: str
    category: StageCategory


@dataclass
class PipelineMetadata:
    """Aggregated metadata from all stages in a pipeline run."""
    task_id: str = ""
    stages: list[dict[str, Any]] = field(default_factory=list)

    def record_stage(self, result: StageResult) -> None:
        self.stages.append({
            "stage_name": result.stage_name,
            "status": result.status,
            "duration_ms": round(result.duration_ms, 1),
            "errors": result.errors,
            "warnings": result.warnings,
            "metadata": result.metadata,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stages": self.stages,
            "overall_status": self._overall_status(),
        }

    def _overall_status(self) -> str:
        statuses = [s["status"] for s in self.stages]
        if "failed" in statuses:
            return "failed"
        if "degraded" in statuses or "skipped" in statuses:
            return "degraded"
        return "success"


class StageNoOpError(Exception):
    """Raised when a stage completes but did zero useful work.

    This allows run_stage to distinguish "success with work done" from
    "success with nothing done" and mark the stage as degraded.
    """


def run_stage(spec: StageSpec, fn: Any, *args: Any, **kwargs: Any) -> StageResult:
    """Execute a stage function and wrap its return in StageResult.

    Handles timing, exception catching, and status assignment based
    on the stage's category (required vs optional).

    Stage functions can optionally return a tuple ``(data, metrics_dict)``
    where ``metrics_dict`` is a dict of work metrics (e.g.
    ``{"images_processed": 10, "images_succeeded": 8}``). These are
    stored in ``StageResult.metadata`` for observability.

    Stage functions can raise ``StageNoOpError`` to signal that they
    completed without doing useful work (e.g. 0 images got AI alt text).
    This is treated as "degraded" for required_with_fallback stages.
    """
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed = (time.monotonic() - start) * 1000

        # Support tuple return: (data, metrics_dict)
        data = result
        metrics: dict[str, Any] = {}
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
            data, metrics = result

        return StageResult(
            stage_name=spec.name,
            status="success",
            data=data,
            duration_ms=elapsed,
            metadata=metrics,
        )
    except StageNoOpError as exc:
        elapsed = (time.monotonic() - start) * 1000
        warning_msg = f"Stage completed with no work done: {exc}"
        if spec.category == "required_with_fallback":
            return StageResult(
                stage_name=spec.name,
                status="degraded",
                data=getattr(exc, "data", None),
                warnings=[warning_msg],
                duration_ms=elapsed,
            )
        elif spec.category == "optional":
            return StageResult(
                stage_name=spec.name,
                status="skipped",
                warnings=[warning_msg],
                duration_ms=elapsed,
            )
        else:  # required — no-op on a required stage is a failure
            return StageResult(
                stage_name=spec.name,
                status="failed",
                errors=[warning_msg],
                duration_ms=elapsed,
            )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        error_msg = f"{type(exc).__name__}: {exc}"
        if spec.category == "required":
            return StageResult(
                stage_name=spec.name,
                status="failed",
                errors=[error_msg],
                duration_ms=elapsed,
            )
        elif spec.category == "required_with_fallback":
            return StageResult(
                stage_name=spec.name,
                status="degraded",
                warnings=[error_msg],
                duration_ms=elapsed,
            )
        else:  # optional
            return StageResult(
                stage_name=spec.name,
                status="skipped",
                warnings=[error_msg],
                duration_ms=elapsed,
            )
