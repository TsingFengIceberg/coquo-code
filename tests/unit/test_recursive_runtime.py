from __future__ import annotations

from threading import Lock
import time

import pytest

from coquo.recursive_orchestration import RecursiveNodeStatus, RecursiveOrchestrationStore
from coquo.recursive_runtime import (
    RecursiveExecutionPolicy,
    RecursiveNodePlan,
    RecursiveRuntime,
    RecursiveRuntimeError,
)


def test_recursive_runtime_runs_admitted_nodes_in_parallel_and_persists_terminal_state(tmp_path):
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    first = store.spawn_child(tree.root.node_id, "first")
    second = store.spawn_child(tree.root.node_id, "second")
    lock = Lock()
    active = 0
    peak = 0

    def run(node, _cancel):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return node.node_id

    result = RecursiveRuntime(store, policy=RecursiveExecutionPolicy(max_parallel=2)).run(
        (RecursiveNodePlan(first.node_id, run), RecursiveNodePlan(second.node_id, run))
    )
    assert {item.status for item in result} == {RecursiveNodeStatus.COMPLETED}
    assert peak == 2
    assert all(node.status is RecursiveNodeStatus.COMPLETED for node in store.inspect().nodes)


def test_recursive_runtime_failure_and_cancellation_are_durable(tmp_path):
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    failed = store.spawn_child(tree.root.node_id, "failed")
    runtime = RecursiveRuntime(store)
    result = runtime.run((RecursiveNodePlan(failed.node_id, lambda _node, _cancel: 1 / 0),))
    assert result[0].status is RecursiveNodeStatus.FAILED
    assert store.inspect().nodes[0].status is RecursiveNodeStatus.FAILED

    cancelled = store.spawn_child(tree.root.node_id, "cancelled")
    cancelled_runtime = RecursiveRuntime(store)
    cancelled_runtime.cancel()
    result = cancelled_runtime.run((RecursiveNodePlan(cancelled.node_id, lambda *_: "never"),))
    assert result[0].cancelled is True
    assert result[0].status is RecursiveNodeStatus.CANCELLED


def test_recursive_runtime_rejects_unknown_and_per_depth_overflow(tmp_path):
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    child = store.spawn_child(tree.root.node_id, "child")
    runtime = RecursiveRuntime(store, policy=RecursiveExecutionPolicy(max_per_depth=1))
    sibling = store.spawn_child(tree.root.node_id, "sibling")
    with pytest.raises(RecursiveRuntimeError, match="per-depth"):
        runtime.run(
            (
                RecursiveNodePlan(child.node_id, lambda *_: None),
                RecursiveNodePlan(sibling.node_id, lambda *_: None),
            )
        )
    with pytest.raises(RecursiveRuntimeError, match="unknown"):
        runtime.run((RecursiveNodePlan("not-a-node", lambda *_: None),))


def test_recursive_runtime_recovery_marks_running_nodes_interrupted(tmp_path):
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    node = store.spawn_child(tree.root.node_id, "orphan")
    store.transition(node.node_id, RecursiveNodeStatus.RUNNING)
    recovered = RecursiveRuntime(store).recover()
    assert recovered[0].status is RecursiveNodeStatus.INTERRUPTED
