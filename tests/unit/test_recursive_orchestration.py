from __future__ import annotations

import pytest

from coquo.recursive_orchestration import (
    RecursiveNodeKind,
    RecursiveNodeStatus,
    RecursiveOrchestrationError,
    RecursiveOrchestrationStore,
    RecursivePolicy,
)


def test_recursive_store_persists_lineage_and_nested_team(tmp_path) -> None:
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create(objective="root objective")
    child = store.spawn_child(tree.root.node_id, "inspect one")
    team = store.spawn_team(tree.root.node_id, "coordinate nested team")
    grandchild = store.spawn_child(child.node_id, "inspect detail")

    assert child.depth == 1
    assert child.parent_node_id == tree.root.node_id
    assert team.kind is RecursiveNodeKind.TEAM
    assert grandchild.depth == 2
    assert grandchild.root_node_id == tree.root.node_id
    assert store.inspect().all_nodes == (tree.root, child, team, grandchild)


def test_recursive_store_enforces_permission_and_child_team_boundaries(tmp_path) -> None:
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    child = store.spawn_child(tree.root.node_id, "inspect")

    with pytest.raises(RecursiveOrchestrationError, match="Child cannot create a Team"):
        store.spawn_team(child.node_id, "nested team")

    writer_workspace = tmp_path / "writer"
    writer_workspace.mkdir()
    writer_store = RecursiveOrchestrationStore(writer_workspace)
    writer_tree = writer_store.create(permission_mode="workspace-write")
    writer = writer_store.spawn_team(
        writer_tree.root.node_id, "writer team", permission_mode="workspace-write"
    )
    with pytest.raises(RecursiveOrchestrationError, match="only read-only descendants"):
        writer_store.spawn_child(writer.node_id, "escalate", permission_mode="danger-full-access")
    with pytest.raises(RecursiveOrchestrationError, match="exceed"):
        writer_store.spawn_team(
            writer.node_id, "escalate team", permission_mode="danger-full-access"
        )


def test_recursive_store_rejects_depth_and_node_overflow(tmp_path) -> None:
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    first = store.spawn_child(tree.root.node_id, "one")
    second = store.spawn_child(first.node_id, "two")
    third = store.spawn_child(second.node_id, "three")
    assert third.depth == 3
    with pytest.raises(RecursiveOrchestrationError, match="depth limit"):
        store.spawn_child(third.node_id, "four")


def test_recursive_store_terminal_nodes_cannot_spawn_or_change_state(tmp_path) -> None:
    store = RecursiveOrchestrationStore(tmp_path)
    tree = store.create()
    child = store.spawn_child(tree.root.node_id, "inspect")
    completed = store.transition(child.node_id, RecursiveNodeStatus.COMPLETED)
    assert completed.status is RecursiveNodeStatus.COMPLETED
    with pytest.raises(RecursiveOrchestrationError, match="terminal node"):
        store.spawn_child(child.node_id, "after completion")
    with pytest.raises(RecursiveOrchestrationError, match="terminal recursive node"):
        store.transition(child.node_id, RecursiveNodeStatus.FAILED)


def test_recursive_policy_has_conservative_bounds() -> None:
    policy = RecursivePolicy()
    assert policy.max_depth == 3
    assert policy.max_nodes == 16
