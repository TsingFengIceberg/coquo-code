"""Deterministic offline evaluation surface for the Leonervis Host."""

from leonervis_code.evals.baseline import (
    DETERMINISTIC_BASELINE_ID,
    EvalError,
    EvalCaseResult,
    EvalSuiteResult,
    builtin_eval_cases,
    render_eval_result_json,
    render_eval_result_text,
    run_eval_case,
    run_eval_suite,
)

__all__ = [
    "DETERMINISTIC_BASELINE_ID",
    "EvalError",
    "EvalCaseResult",
    "EvalSuiteResult",
    "builtin_eval_cases",
    "render_eval_result_json",
    "render_eval_result_text",
    "run_eval_case",
    "run_eval_suite",
]
