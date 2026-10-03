"""Evaluation and benchmark plugins."""

from tradearena.evaluation.audit import AuditManifest, export_audit_bundle
from tradearena.evaluation.autopsy import FAILURE_MODES, autopsy_trajectory, classify_step_failure_modes
from tradearena.evaluation.benchmarks import BenchmarkCase, BenchmarkRunner
from tradearena.evaluation.defect_injection import (
    DEFECT_KINDS,
    applicable_steps,
    inject_defect,
    score_findings,
)
from tradearena.evaluation.metrics import (
    BehavioralEvaluator,
    DecisionQualityEvaluator,
    ExecutionRealismEvaluator,
    IntentExecutionGapEvaluator,
    PerformanceEvaluator,
    ReasoningConsistencyEvaluator,
    RiskAuditEvaluator,
)
from tradearena.evaluation.offline_tracking import export_trajectory_to_offline_tracking, trajectory_to_offline_tracking
from tradearena.evaluation.submissions import (
    build_registry_rows,
    validate_submission,
    validate_submission_file,
    write_registry_html,
    write_registry_markdown,
)
from tradearena.evaluation.tasks import TRADEARENA_CORE_TASKS, BenchmarkTask, DataLeakagePolicy
from tradearena.evaluation.trace_export import export_trajectory_to_trace_json, trajectory_to_trace
from tradearena.evaluation.trace_schema_export import (
    export_trajectory_to_trace_schema_json,
    trajectory_to_eval_trace_schema,
)

__all__ = [
    "AuditManifest",
    "BehavioralEvaluator",
    "BenchmarkCase",
    "BenchmarkRunner",
    "BenchmarkTask",
    "DataLeakagePolicy",
    "DEFECT_KINDS",
    "DecisionQualityEvaluator",
    "ExecutionRealismEvaluator",
    "FAILURE_MODES",
    "IntentExecutionGapEvaluator",
    "applicable_steps",
    "inject_defect",
    "score_findings",
    "PerformanceEvaluator",
    "ReasoningConsistencyEvaluator",
    "RiskAuditEvaluator",
    "TRADEARENA_CORE_TASKS",
    "autopsy_trajectory",
    "build_registry_rows",
    "classify_step_failure_modes",
    "export_audit_bundle",
    "export_trajectory_to_offline_tracking",
    "export_trajectory_to_trace_json",
    "export_trajectory_to_trace_schema_json",
    "trajectory_to_offline_tracking",
    "trajectory_to_trace",
    "trajectory_to_eval_trace_schema",
    "validate_submission",
    "validate_submission_file",
    "write_registry_html",
    "write_registry_markdown",
]
