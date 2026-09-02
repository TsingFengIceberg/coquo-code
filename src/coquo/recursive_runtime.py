"""Bounded execution runtime for Host-owned recursive orchestration trees.

The lineage store remains the durable authority.  This module only schedules
already-admitted nodes through a bounded executor and records every transition
before exposing the result to callers.  Callbacks are Host code and receive no
implicit permissions or tools.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Event, Lock
from typing import Callable, Iterable

from coquo.recursive_orchestration import (
    RecursiveNode,
    RecursiveNodeStatus,
    RecursiveOrchestrationError,
    RecursiveOrchestrationStore,
)


class RecursiveRuntimeError(RuntimeError):
    """Raised when bounded recursive execution cannot proceed safely."""


@dataclass(frozen=True)
class RecursiveExecutionPolicy:
    """Finite resources for one execution wave/tree."""

    max_parallel: int = 4
    max_total: int = 16
    max_per_depth: int = 8

    def __post_init__(self) -> None:
        if type(self.max_parallel) is not int or not 1 <= self.max_parallel <= 8:
            raise ValueError("max_parallel must be between 1 and 8")
        if type(self.max_total) is not int or not 1 <= self.max_total <= 64:
            raise ValueError("max_total must be between 1 and 64")
        if type(self.max_per_depth) is not int or not 1 <= self.max_per_depth <= 32:
            raise ValueError("max_per_depth must be between 1 and 32")


@dataclass(frozen=True)
class RecursiveNodePlan:
    """A node already admitted in the lineage store and its Host callback."""

    node_id: str
    run: Callable[[RecursiveNode, Event], object]

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id is required")
        if not callable(self.run):
            raise ValueError("run callback is required")


@dataclass(frozen=True)
class RecursiveExecutionResult:
    node_id: str
    status: RecursiveNodeStatus
    value: object | None = None
    error: str | None = None
    cancelled: bool = False


class RecursiveRuntime:
    """Execute a bounded set of admitted recursive nodes, safely and in parallel."""

    def __init__(
        self,
        store: RecursiveOrchestrationStore,
        *,
        policy: RecursiveExecutionPolicy | None = None,
        executor_factory: Callable[[int], ThreadPoolExecutor] | None = None,
    ) -> None:
        if not isinstance(store, RecursiveOrchestrationStore):
            raise ValueError("recursive orchestration store is required")
        self.store = store
        self.policy = policy or RecursiveExecutionPolicy()
        self._executor_factory = executor_factory or (lambda size: ThreadPoolExecutor(size))
        self._cancel = Event()
        self._guard = Lock()
        self._futures: dict[str, Future[RecursiveExecutionResult]] = {}

    @property
    def cancellation(self) -> Event:
        return self._cancel

    def cancel(self) -> None:
        """Request cooperative cancellation for queued and running callbacks."""
        self._cancel.set()

    def run(self, plans: Iterable[RecursiveNodePlan]) -> tuple[RecursiveExecutionResult, ...]:
        selected = tuple(plans)
        if len(selected) > self.policy.max_total:
            raise RecursiveRuntimeError("recursive execution total limit reached")
        if len({plan.node_id for plan in selected}) != len(selected):
            raise RecursiveRuntimeError("recursive execution contains duplicate nodes")
        tree = self.store.inspect()
        nodes = {node.node_id: node for node in tree.all_nodes}
        for plan in selected:
            node = nodes.get(plan.node_id)
            if node is None:
                raise RecursiveRuntimeError("execution plan references unknown node")
            if node.status.terminal:
                raise RecursiveRuntimeError("execution plan references terminal node")
        by_depth: dict[int, int] = {}
        for plan in selected:
            depth = nodes[plan.node_id].depth
            by_depth[depth] = by_depth.get(depth, 0) + 1
        if any(count > self.policy.max_per_depth for count in by_depth.values()):
            raise RecursiveRuntimeError("recursive per-depth limit reached")

        results: list[RecursiveExecutionResult] = []
        futures: dict[str, Future[RecursiveExecutionResult]] = {}
        with self._executor_factory(min(self.policy.max_parallel, max(1, len(selected)))) as pool:
            for plan in selected:
                node = nodes[plan.node_id]
                try:
                    self.store.transition(node.node_id, RecursiveNodeStatus.RUNNING)
                except RecursiveOrchestrationError as error:
                    raise RecursiveRuntimeError(str(error)) from error
                future = pool.submit(self._execute_one, node, plan)
                futures[node.node_id] = future
                with self._guard:
                    self._futures[node.node_id] = future
            for future in as_completed(tuple(futures.values())):
                results.append(future.result())
        with self._guard:
            for node_id in futures:
                self._futures.pop(node_id, None)
        results.sort(key=lambda item: item.node_id)
        return tuple(results)

    def recover(self) -> tuple[RecursiveNode, ...]:
        """Fail closed on nodes left running by a crashed Host."""
        tree = self.store.inspect()
        recovered: list[RecursiveNode] = []
        for node in tree.all_nodes:
            if node.status is RecursiveNodeStatus.RUNNING:
                recovered.append(
                    self.store.transition(node.node_id, RecursiveNodeStatus.INTERRUPTED)
                )
        return tuple(recovered)

    def reset_cancellation(self) -> None:
        """Clear a prior cancellation request before a new execution wave."""
        self._cancel.clear()

    def _execute_one(
        self, node: RecursiveNode, plan: RecursiveNodePlan
    ) -> RecursiveExecutionResult:
        if self._cancel.is_set():
            status = self.store.transition(node.node_id, RecursiveNodeStatus.CANCELLED)
            return RecursiveExecutionResult(node.node_id, status.status, cancelled=True)
        try:
            value = plan.run(node, self._cancel)
            status = (
                RecursiveNodeStatus.CANCELLED
                if self._cancel.is_set()
                else RecursiveNodeStatus.COMPLETED
            )
            persisted = self.store.transition(node.node_id, status)
            return RecursiveExecutionResult(
                node.node_id,
                persisted.status,
                value,
                cancelled=status is RecursiveNodeStatus.CANCELLED,
            )
        except Exception as error:  # callback failures become durable terminal facts
            persisted = self.store.transition(node.node_id, RecursiveNodeStatus.FAILED)
            return RecursiveExecutionResult(node.node_id, persisted.status, error=str(error))


__all__ = [
    "RecursiveExecutionPolicy",
    "RecursiveExecutionResult",
    "RecursiveNodePlan",
    "RecursiveRuntime",
    "RecursiveRuntimeError",
]
