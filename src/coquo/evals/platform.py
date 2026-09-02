"""A small, deterministic Eval platform for evolution and release gates.

The existing Host and coding-task evaluators remain the authoritative
executors.  This module adds the missing platform layer: versioned datasets,
closed graders, durable run metadata, baseline/candidate comparison, and a
regression gate.  It stores only bounded, content-free facts and never calls a
Provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from coquo.evals.baseline import builtin_eval_cases, run_eval_suite


MAX_DATASETS = 128
MAX_CASES_PER_DATASET = 256
MAX_RUNS = 2_000
MAX_CHECKS_PER_CASE = 64
MAX_NAME_BYTES = 128
MAX_EVAL_LOG_BYTES = 8 * 1024 * 1024


class EvalPlatformError(ValueError):
    """Raised for invalid dataset, grader, run, or regression state."""


def _text(value: Any, field: str, limit: int = MAX_NAME_BYTES) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EvalPlatformError(f"Eval {field} must be non-blank text")
    if "\x00" in value or len(value.encode("utf-8")) > limit:
        raise EvalPlatformError(f"Eval {field} exceeds its bound")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvalDataset:
    """Immutable case inventory with disjoint validation and test splits."""

    dataset_id: str
    version: int
    case_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        _text(self.dataset_id, "dataset ID")
        if type(self.version) is not int or not 1 <= self.version <= 10_000:
            raise EvalPlatformError("Eval dataset version is invalid")
        if not self.case_ids or len(self.case_ids) > MAX_CASES_PER_DATASET:
            raise EvalPlatformError("Eval dataset case set is invalid")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise EvalPlatformError("Eval dataset case IDs must be unique")
        for case_id in self.case_ids:
            _text(case_id, "case ID")
        known = set(self.case_ids)
        validation, test = set(self.validation_ids), set(self.test_ids)
        if not validation or not test or validation & test:
            raise EvalPlatformError(
                "Eval validation and test splits must be non-empty and disjoint"
            )
        if not validation <= known or not test <= known:
            raise EvalPlatformError("Eval split references an unknown case")
        if self.description:
            _text(self.description, "dataset description", 512)

    def as_mapping(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "case_ids": list(self.case_ids),
            "validation_ids": list(self.validation_ids),
            "test_ids": list(self.test_ids),
            "description": self.description,
        }


@dataclass(frozen=True)
class EvalGrade:
    """One closed grader result; checks contain names, not raw task content."""

    case_id: str
    passed: bool
    score: float
    checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.case_id, "case ID")
        if (
            type(self.passed) is not bool
            or isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
        ):
            raise EvalPlatformError("Eval grade values are invalid")
        if not 0.0 <= float(self.score) <= 1.0:
            raise EvalPlatformError("Eval grade score must be between 0 and 1")
        if len(self.checks) > MAX_CHECKS_PER_CASE or any(
            not isinstance(item, str) or not item for item in self.checks
        ):
            raise EvalPlatformError("Eval grade checks are invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": float(self.score),
            "checks": list(self.checks),
        }


@dataclass(frozen=True)
class EvalRun:
    """One reproducible dataset run with stable content projection."""

    run_id: str
    dataset_id: str
    dataset_version: int
    label: str
    grades: tuple[EvalGrade, ...]
    started_at: str
    finished_at: str

    def __post_init__(self) -> None:
        _text(self.run_id, "run ID", 64)
        _text(self.dataset_id, "dataset ID")
        _text(self.label, "run label")
        if type(self.dataset_version) is not int or self.dataset_version < 1:
            raise EvalPlatformError("Eval run dataset version is invalid")
        if not self.grades or len(self.grades) > MAX_CASES_PER_DATASET:
            raise EvalPlatformError("Eval run grades are invalid")
        if len({grade.case_id for grade in self.grades}) != len(self.grades):
            raise EvalPlatformError("Eval run case grades must be unique")
        _text(self.started_at, "run start timestamp", 64)
        _text(self.finished_at, "run finish timestamp", 64)

    @property
    def passed_cases(self) -> int:
        return sum(grade.passed for grade in self.grades)

    @property
    def pass_rate(self) -> float:
        return self.passed_cases / len(self.grades)

    @property
    def mean_score(self) -> float:
        return sum(float(grade.score) for grade in self.grades) / len(self.grades)

    def as_mapping(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "label": self.label,
            "grades": [grade.as_mapping() for grade in self.grades],
            "summary": {
                "passed_cases": self.passed_cases,
                "total_cases": len(self.grades),
                "pass_rate": self.pass_rate,
                "mean_score": self.mean_score,
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def stable_projection(self) -> dict[str, object]:
        payload = self.as_mapping()
        payload.pop("run_id", None)
        payload.pop("started_at", None)
        payload.pop("finished_at", None)
        return payload


@dataclass(frozen=True)
class EvalComparison:
    dataset_id: str
    baseline_run_id: str
    candidate_run_id: str
    passed: bool
    checks: tuple[str, ...]
    regressions: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "baseline_run_id": self.baseline_run_id,
            "candidate_run_id": self.candidate_run_id,
            "passed": self.passed,
            "checks": list(self.checks),
            "regressions": list(self.regressions),
        }


class EvalRunStore:
    """Bounded local run ledger; it never stores prompts or workspace paths."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir() or self.workspace.is_symlink():
            raise EvalPlatformError("Eval workspace must be an existing non-symlink directory")
        self.root = self.workspace / ".coquo" / "evals"
        self.path = self.root / "runs.jsonl"
        self.lock = RLock()

    def append(self, run: EvalRun) -> None:
        raw = (
            json.dumps({"event": "run", "version": 1, **run.as_mapping()}, sort_keys=True) + "\n"
        ).encode()
        if len(raw) > 128 * 1024:
            raise EvalPlatformError("Eval run record exceeds its size bound")
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.exists() and self.path.stat().st_size + len(raw) > MAX_EVAL_LOG_BYTES:
                raise EvalPlatformError("Eval run log exceeds its size bound")
            with self.path.open("ab") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

    def runs(self) -> tuple[EvalRun, ...]:
        if not self.path.exists():
            return ()
        if self.path.stat().st_size > MAX_EVAL_LOG_BYTES:
            raise EvalPlatformError("Eval run log exceeds its size bound")
        result: list[EvalRun] = []
        for line in self.path.read_bytes().splitlines():
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise EvalPlatformError("Eval run log is invalid") from error
            if (
                not isinstance(value, dict)
                or value.get("event") != "run"
                or value.get("version") != 1
            ):
                raise EvalPlatformError("Eval run event schema is invalid")
            grades = tuple(
                EvalGrade(
                    item["case_id"], item["passed"], item["score"], tuple(item.get("checks", ()))
                )
                for item in value.get("grades", ())
            )
            result.append(
                EvalRun(
                    value["run_id"],
                    value["dataset_id"],
                    value["dataset_version"],
                    value["label"],
                    grades,
                    value["started_at"],
                    value["finished_at"],
                )
            )
            if len(result) > MAX_RUNS:
                raise EvalPlatformError("Eval run count exceeds its bound")
        return tuple(result)


class EvalPlatform:
    """Dataset registry, deterministic graders, run persistence and gates."""

    def __init__(self, workspace: Path | None = None) -> None:
        self._datasets: dict[str, EvalDataset] = {}
        self._store = EvalRunStore(workspace) if workspace is not None else None
        builtin = builtin_eval_cases()
        self.register(
            EvalDataset(
                "host-baseline-v3",
                1,
                tuple(case.case_id for case in builtin),
                tuple(case.case_id for case in builtin[: max(1, len(builtin) // 2)]),
                tuple(case.case_id for case in builtin[max(1, len(builtin) // 2) :]),
                "Canonical deterministic Host evaluation suite",
            )
        )

    def register(self, dataset: EvalDataset) -> EvalDataset:
        if not isinstance(dataset, EvalDataset):
            raise EvalPlatformError("Eval dataset is invalid")
        if dataset.dataset_id not in self._datasets and len(self._datasets) >= MAX_DATASETS:
            raise EvalPlatformError("Eval dataset count exceeds its bound")
        self._datasets[dataset.dataset_id] = dataset
        return dataset

    def datasets(self) -> tuple[EvalDataset, ...]:
        return tuple(self._datasets[key] for key in sorted(self._datasets))

    def dataset(self, dataset_id: str) -> EvalDataset:
        try:
            return self._datasets[dataset_id]
        except KeyError:
            raise EvalPlatformError(f"unknown Eval dataset: {dataset_id}") from None

    def run_builtin(
        self, dataset_id: str = "host-baseline-v3", *, label: str = "baseline"
    ) -> EvalRun:
        dataset = self.dataset(dataset_id)
        if dataset.dataset_id != "host-baseline-v3":
            raise EvalPlatformError("only the canonical Host dataset has a built-in executor")
        suite = run_eval_suite()
        grades = tuple(self._grade_case(case) for case in suite.cases)
        run = EvalRun(
            str(uuid4()), dataset.dataset_id, dataset.version, label, grades, _now(), _now()
        )
        if self._store is not None:
            self._store.append(run)
        return run

    def grade(
        self,
        dataset_id: str,
        observations: Mapping[str, Mapping[str, Any]],
        *,
        label: str = "candidate",
    ) -> EvalRun:
        dataset = self.dataset(dataset_id)
        if not isinstance(observations, Mapping):
            raise EvalPlatformError("Eval observations must be an object")
        grades: list[EvalGrade] = []
        for case_id in dataset.case_ids:
            raw = observations.get(case_id)
            if not isinstance(raw, Mapping):
                raise EvalPlatformError(f"missing Eval observation: {case_id}")
            passed = raw.get("passed")
            score = raw.get("score", 1.0 if passed else 0.0)
            checks = tuple(raw.get("checks", ()))
            grades.append(EvalGrade(case_id, passed is True, score, checks))
        run = EvalRun(
            str(uuid4()), dataset.dataset_id, dataset.version, label, tuple(grades), _now(), _now()
        )
        if self._store is not None:
            self._store.append(run)
        return run

    def compare(self, baseline: EvalRun, candidate: EvalRun) -> EvalComparison:
        if not isinstance(baseline, EvalRun) or not isinstance(candidate, EvalRun):
            raise EvalPlatformError("Eval comparison runs are invalid")
        if (baseline.dataset_id, baseline.dataset_version) != (
            candidate.dataset_id,
            candidate.dataset_version,
        ):
            raise EvalPlatformError("Eval comparison requires the same dataset version")
        base = {grade.case_id: grade for grade in baseline.grades}
        newer = {grade.case_id: grade for grade in candidate.grades}
        regressions: list[str] = []
        checks: list[str] = []
        for case_id in sorted(set(base) | set(newer)):
            if case_id not in base or case_id not in newer:
                regressions.append(f"{case_id}:missing")
                continue
            if newer[case_id].score < base[case_id].score:
                regressions.append(f"{case_id}:score")
            if base[case_id].passed and not newer[case_id].passed:
                regressions.append(f"{case_id}:pass")
            checks.append(f"{case_id}:{'ok' if case_id not in regressions else 'regression'}")
        if candidate.pass_rate < baseline.pass_rate:
            regressions.append("suite:pass_rate")
        if candidate.mean_score < baseline.mean_score:
            regressions.append("suite:mean_score")
        return EvalComparison(
            baseline.dataset_id,
            baseline.run_id,
            candidate.run_id,
            not regressions,
            tuple(checks),
            tuple(dict.fromkeys(regressions)),
        )

    @staticmethod
    def gate(comparison: EvalComparison) -> None:
        if not isinstance(comparison, EvalComparison):
            raise EvalPlatformError("Eval comparison is invalid")
        if not comparison.passed:
            raise EvalPlatformError(
                "Eval regression gate failed: " + ", ".join(comparison.regressions)
            )

    def runs(self) -> tuple[EvalRun, ...]:
        return () if self._store is None else self._store.runs()

    @staticmethod
    def _grade_case(case) -> EvalGrade:
        passed = case.passed
        score = case.passed_checks / len(case.checks) if case.checks else 0.0
        failed = tuple(check.name for check in case.checks if not check.passed)
        return EvalGrade(case.case_id, passed, score, failed)


__all__ = [
    "EvalComparison",
    "EvalDataset",
    "EvalGrade",
    "EvalPlatform",
    "EvalPlatformError",
    "EvalRun",
    "EvalRunStore",
]
