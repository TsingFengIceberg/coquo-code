"""Host-owned recursive Child/Team orchestration state.

The existing Child and Team runtimes own execution details.  This module owns
the small amount of cross-runtime state needed when a Host schedules nested
delegation: immutable lineage, bounded depth/node admission, capability
ceiling checks, and an append-only recovery ledger.  It deliberately exposes
no model-facing tools and never grants a child additional permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from typing import Callable
from uuid import UUID, uuid4

from coquo.core.permissions import PermissionMode
from coquo.session_records import workspace_fingerprint


RECURSIVE_ORCHESTRATION_SCHEMA_VERSION = 1
MAX_RECURSIVE_DEPTH = 4
MAX_RECURSIVE_NODES = 64
MAX_RECURSIVE_OBJECTIVE_BYTES = 16 * 1024
MAX_RECURSIVE_RECORDS = 10_000
MAX_RECURSIVE_RECORD_BYTES = 32 * 1024
_UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")


class RecursiveOrchestrationError(RuntimeError):
    """Raised when nested delegation cannot be admitted or replayed safely."""


class RecursiveNodeKind(StrEnum):
    ROOT = "root"
    TASK = "task"
    CHILD = "child"
    TEAM = "team"


class RecursiveNodeStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            RecursiveNodeStatus.COMPLETED,
            RecursiveNodeStatus.FAILED,
            RecursiveNodeStatus.CANCELLED,
            RecursiveNodeStatus.INTERRUPTED,
        }


@dataclass(frozen=True)
class RecursivePolicy:
    """Closed Host policy for one recursive orchestration tree."""

    max_depth: int = 3
    max_nodes: int = 16
    allow_nested_teams: bool = True
    allow_child_delegation: bool = True

    def __post_init__(self) -> None:
        if type(self.max_depth) is not int or not 1 <= self.max_depth <= MAX_RECURSIVE_DEPTH:
            raise ValueError(f"max_depth must be between 1 and {MAX_RECURSIVE_DEPTH}")
        if type(self.max_nodes) is not int or not 1 <= self.max_nodes <= MAX_RECURSIVE_NODES:
            raise ValueError(f"max_nodes must be between 1 and {MAX_RECURSIVE_NODES}")
        if (
            type(self.allow_nested_teams) is not bool
            or type(self.allow_child_delegation) is not bool
        ):
            raise ValueError("recursive policy flags are invalid")


@dataclass(frozen=True)
class RecursiveNode:
    node_id: str
    root_node_id: str
    parent_node_id: str | None
    depth: int
    kind: RecursiveNodeKind
    status: RecursiveNodeStatus
    permission_mode: str
    capability: str
    objective_sha256: str
    created_at: str
    updated_at: str
    child_run_id: str | None = None
    team_id: str | None = None
    team_assignment_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in ((self.node_id, "node_id"), (self.root_node_id, "root_node_id")):
            if not isinstance(value, str) or not _UUID4.fullmatch(value):
                raise ValueError(f"{label} is invalid")
        if self.parent_node_id is not None and not _UUID4.fullmatch(self.parent_node_id):
            raise ValueError("parent_node_id is invalid")
        if type(self.depth) is not int or not 0 <= self.depth <= MAX_RECURSIVE_DEPTH:
            raise ValueError("recursive node depth is invalid")
        if self.depth == 0 and self.parent_node_id is not None:
            raise ValueError("root node cannot have a parent")
        if self.depth > 0 and self.parent_node_id is None:
            raise ValueError("nested node requires a parent")
        if self.kind is RecursiveNodeKind.ROOT and self.depth != 0:
            raise ValueError("root node depth is invalid")
        if self.kind is not RecursiveNodeKind.ROOT and self.depth == 0:
            raise ValueError("non-root node cannot be depth zero")
        if self.permission_mode not in {mode.value for mode in PermissionMode}:
            raise ValueError("recursive node permission mode is invalid")
        if not self.capability or len(self.capability) > 128:
            raise ValueError("recursive node capability is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}\Z", self.objective_sha256):
            raise ValueError("recursive node objective digest is invalid")
        for value, label in ((self.created_at, "created_at"), (self.updated_at, "updated_at")):
            if not isinstance(value, str) or not value or len(value) > 64:
                raise ValueError(f"{label} is invalid")
        for value, label in (
            (self.child_run_id, "child_run_id"),
            (self.team_id, "team_id"),
            (self.team_assignment_id, "team_assignment_id"),
            (self.task_id, "task_id"),
        ):
            if value is not None and not _UUID4.fullmatch(value):
                raise ValueError(f"{label} is invalid")


@dataclass(frozen=True)
class RecursiveTree:
    root: RecursiveNode
    nodes: tuple[RecursiveNode, ...]
    policy: RecursivePolicy

    def __post_init__(self) -> None:
        if not isinstance(self.root, RecursiveNode) or self.root.kind is not RecursiveNodeKind.ROOT:
            raise ValueError("recursive tree root is invalid")
        if not isinstance(self.nodes, tuple) or len(self.nodes) > self.policy.max_nodes:
            raise ValueError("recursive tree node count exceeds policy")
        ids = {self.root.node_id}
        for node in self.nodes:
            if not isinstance(node, RecursiveNode) or node.node_id in ids:
                raise ValueError("recursive tree contains duplicate or invalid node")
            if node.root_node_id != self.root.node_id:
                raise ValueError("recursive tree root lineage is invalid")
            ids.add(node.node_id)

    @property
    def all_nodes(self) -> tuple[RecursiveNode, ...]:
        return (self.root, *self.nodes)

    @property
    def terminal(self) -> bool:
        return self.root.status.terminal


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid4(value: object, label: str) -> str:
    if not isinstance(value, str) or not _UUID4.fullmatch(value):
        raise RecursiveOrchestrationError(f"{label} must be a canonical UUID4")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError):
        raise RecursiveOrchestrationError(f"{label} must be a canonical UUID4") from None
    if parsed.version != 4:
        raise RecursiveOrchestrationError(f"{label} must be a UUID4")
    return value


def _objective_digest(objective: str) -> str:
    if not isinstance(objective, str) or not objective.strip():
        raise RecursiveOrchestrationError("recursive objective must not be blank")
    encoded = objective.encode("utf-8")
    if len(encoded) > MAX_RECURSIVE_OBJECTIVE_BYTES:
        raise RecursiveOrchestrationError("recursive objective exceeds the byte limit")
    return hashlib.sha256(encoded).hexdigest()


def _permission_ceiling(parent: str, child: str) -> str:
    order = {
        PermissionMode.READ_ONLY.value: 0,
        PermissionMode.WORKSPACE_WRITE.value: 1,
        PermissionMode.DANGER_FULL_ACCESS.value: 2,
    }
    if parent not in order or child not in order:
        raise RecursiveOrchestrationError("permission mode is invalid")
    if order[child] > order[parent]:
        raise RecursiveOrchestrationError("nested node cannot exceed parent permission ceiling")
    return child


class RecursiveOrchestrationStore:
    """Durable append-only lineage store for one workspace."""

    def __init__(self, workspace: Path, *, clock: Callable[[], str] = utc_now) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise RecursiveOrchestrationError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise RecursiveOrchestrationError("workspace is inaccessible") from None
        if not resolved.is_dir():
            raise RecursiveOrchestrationError("workspace is not a directory")
        self.workspace = resolved
        self.root = resolved / ".coquo" / "orchestration" / workspace_fingerprint(resolved)
        self.path = self.root / "recursive.jsonl"
        self._clock = clock
        self._guard = Lock()

    def create(
        self, *, permission_mode: str = PermissionMode.READ_ONLY.value, objective: str = "root"
    ) -> RecursiveTree:
        policy = RecursivePolicy()
        now = self._clock()
        node_id = str(uuid4())
        root = RecursiveNode(
            node_id=node_id,
            root_node_id=node_id,
            parent_node_id=None,
            depth=0,
            kind=RecursiveNodeKind.ROOT,
            status=RecursiveNodeStatus.RUNNING,
            permission_mode=permission_mode,
            capability="host-orchestrator-v1",
            objective_sha256=_objective_digest(objective),
            created_at=now,
            updated_at=now,
        )
        with self._guard:
            if self._trees():
                raise RecursiveOrchestrationError("recursive orchestration already exists")
            self._append(
                {"event": "created", "policy": _policy_mapping(policy), "node": _node_mapping(root)}
            )
        return RecursiveTree(root, (), policy)

    def inspect(self) -> RecursiveTree:
        with self._guard:
            records = self._read()
        if not records:
            raise RecursiveOrchestrationError("recursive orchestration was not found")
        return self._replay(records)

    def spawn_child(
        self,
        parent_node_id: str,
        objective: str,
        *,
        permission_mode: str = PermissionMode.READ_ONLY.value,
        capability: str = "read-only-explorer-v1",
        child_run_id: str | None = None,
    ) -> RecursiveNode:
        return self._spawn(
            parent_node_id,
            RecursiveNodeKind.CHILD,
            objective,
            permission_mode=permission_mode,
            capability=capability,
            child_run_id=child_run_id,
        )

    def project_child(
        self,
        parent_node_id: str,
        objective: str,
        *,
        permission_mode: str = PermissionMode.READ_ONLY.value,
        capability: str = "read-only-explorer-v1",
        child_run_id: str | None = None,
    ) -> RecursiveNode:
        """Bind a Host-created Child under an existing Team/Task projection.

        ``spawn_child`` models a Child's own recursive delegation and therefore
        retains the conservative read-only-parent rule.  Team assignments and
        Task bridge admissions are Host operations, so their projection must be
        able to bind a writable Child without granting that Child delegation
        authority.  Both paths still enforce the same depth, node, and
        permission-ceiling checks.
        """
        return self._spawn(
            parent_node_id,
            RecursiveNodeKind.CHILD,
            objective,
            permission_mode=permission_mode,
            capability=capability,
            child_run_id=child_run_id,
            host_projection=True,
        )

    def spawn_team(
        self,
        parent_node_id: str,
        objective: str,
        *,
        permission_mode: str = PermissionMode.READ_ONLY.value,
        capability: str = "team-orchestrator-v1",
        team_id: str | None = None,
        team_assignment_id: str | None = None,
    ) -> RecursiveNode:
        return self._spawn(
            parent_node_id,
            RecursiveNodeKind.TEAM,
            objective,
            permission_mode=permission_mode,
            capability=capability,
            team_id=team_id,
            team_assignment_id=team_assignment_id,
        )

    def spawn_task(
        self,
        parent_node_id: str,
        objective: str,
        *,
        permission_mode: str = PermissionMode.READ_ONLY.value,
        capability: str = "task-orchestrator-v1",
        task_id: str | None = None,
    ) -> RecursiveNode:
        """Project a durable Task into the Host-owned lineage tree."""
        return self._spawn(
            parent_node_id,
            RecursiveNodeKind.TASK,
            objective,
            permission_mode=permission_mode,
            capability=capability,
            task_id=task_id,
        )

    def node_for_child_run(self, child_run_id: str) -> RecursiveNode | None:
        return self._node_for_link("child_run_id", child_run_id)

    def node_for_team(self, team_id: str) -> RecursiveNode | None:
        return self._node_for_link("team_id", team_id)

    def node_for_task(self, task_id: str) -> RecursiveNode | None:
        return self._node_for_link("task_id", task_id)

    def _node_for_link(self, field: str, value: str) -> RecursiveNode | None:
        value = _uuid4(value, field)
        with self._guard:
            if not self.path.exists():
                return None
            tree = self._replay(self._read())
        return next((node for node in tree.all_nodes if getattr(node, field) == value), None)

    def transition(self, node_id: str, status: RecursiveNodeStatus | str) -> RecursiveNode:
        node_id = _uuid4(node_id, "node ID")
        try:
            next_status = RecursiveNodeStatus(status)
        except ValueError:
            raise RecursiveOrchestrationError("recursive node status is invalid") from None
        with self._guard:
            tree = self._replay(self._read())
            current = next((node for node in tree.all_nodes if node.node_id == node_id), None)
            if current is None:
                raise RecursiveOrchestrationError("recursive node was not found")
            if current.status.terminal:
                if current.status is not next_status:
                    raise RecursiveOrchestrationError("terminal recursive node cannot change state")
                return current
            if next_status is RecursiveNodeStatus.QUEUED:
                raise RecursiveOrchestrationError("recursive node cannot return to queued")
            updated = _replace_node(current, status=next_status, updated_at=self._clock())
            self._append({"event": "transition", "node": _node_mapping(updated)})
            return updated

    def _spawn(
        self, parent_node_id: str, kind: RecursiveNodeKind, objective: str, **kwargs
    ) -> RecursiveNode:
        parent_node_id = _uuid4(parent_node_id, "parent node ID")
        permission_mode = kwargs.pop("permission_mode")
        capability = kwargs.pop("capability")
        if kwargs.get("child_run_id") is not None:
            kwargs["child_run_id"] = _uuid4(kwargs["child_run_id"], "Child Run ID")
        host_projection = kwargs.pop("host_projection", False)
        if type(host_projection) is not bool:
            raise RecursiveOrchestrationError("host projection flag is invalid")
        for key in ("team_id", "team_assignment_id", "task_id"):
            if kwargs.get(key) is not None:
                kwargs[key] = _uuid4(kwargs[key], key)
        with self._guard:
            tree = self._replay(self._read())
            parent = next((node for node in tree.all_nodes if node.node_id == parent_node_id), None)
            if parent is None:
                raise RecursiveOrchestrationError("parent recursive node was not found")
            if parent.status.terminal:
                raise RecursiveOrchestrationError("terminal node cannot spawn descendants")
            if parent.depth >= tree.policy.max_depth:
                raise RecursiveOrchestrationError("recursive depth limit reached")
            if len(tree.all_nodes) >= tree.policy.max_nodes:
                raise RecursiveOrchestrationError("recursive node limit reached")
            if host_projection and parent.kind not in {
                RecursiveNodeKind.TASK,
                RecursiveNodeKind.TEAM,
            }:
                raise RecursiveOrchestrationError(
                    "Host Child projection requires a Task or Team parent"
                )
            if kind is RecursiveNodeKind.CHILD:
                if not tree.policy.allow_child_delegation:
                    raise RecursiveOrchestrationError("recursive Child delegation is disabled")
                if not host_projection and (
                    parent.kind is not RecursiveNodeKind.ROOT
                    and parent.permission_mode != PermissionMode.READ_ONLY.value
                ):
                    raise RecursiveOrchestrationError(
                        "only read-only descendants may spawn a Child"
                    )
            elif kind is RecursiveNodeKind.TEAM:
                if not tree.policy.allow_nested_teams:
                    raise RecursiveOrchestrationError("nested Team delegation is disabled")
                if parent.kind is RecursiveNodeKind.CHILD:
                    raise RecursiveOrchestrationError("Child cannot create a Team")
            elif kind is RecursiveNodeKind.TASK:
                if parent.kind is RecursiveNodeKind.CHILD:
                    raise RecursiveOrchestrationError("Child cannot create a Task")
            child_permission = _permission_ceiling(parent.permission_mode, permission_mode)
            now = self._clock()
            node_id = str(uuid4())
            node = RecursiveNode(
                node_id=node_id,
                root_node_id=tree.root.node_id,
                parent_node_id=parent.node_id,
                depth=parent.depth + 1,
                kind=kind,
                status=RecursiveNodeStatus.QUEUED,
                permission_mode=child_permission,
                capability=capability,
                objective_sha256=_objective_digest(objective),
                created_at=now,
                updated_at=now,
                **kwargs,
            )
            self._append({"event": "spawned", "node": _node_mapping(node)})
            return node

    def _trees(self) -> tuple[RecursiveTree, ...]:
        data = self._read()
        return () if not data else (self._replay(data),)

    def _replay(self, records: list[dict[str, object]]) -> RecursiveTree:
        first = records[0]
        if first.get("event") != "created":
            raise RecursiveOrchestrationError("recursive ledger does not start with creation")
        policy = _policy_from_mapping(first.get("policy"))
        root = _node_from_mapping(first.get("node"))
        nodes: dict[str, RecursiveNode] = {}
        for record in records[1:]:
            node = _node_from_mapping(record.get("node"))
            if node.node_id == root.node_id:
                root = node
            else:
                nodes[node.node_id] = node
        for node in nodes.values():
            if node.parent_node_id == node.node_id or (
                node.parent_node_id is not None
                and node.parent_node_id not in {root.node_id, *nodes}
            ):
                raise RecursiveOrchestrationError("recursive ledger contains a broken parent link")
        return RecursiveTree(root, tuple(nodes.values()), policy)

    def _read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            payload = self.path.read_bytes()
        except OSError as error:
            raise RecursiveOrchestrationError("recursive ledger is inaccessible") from error
        if len(payload) > MAX_RECURSIVE_RECORDS * MAX_RECURSIVE_RECORD_BYTES or (
            payload and not payload.endswith(b"\n")
        ):
            raise RecursiveOrchestrationError("recursive ledger is not at a durable boundary")
        records: list[dict[str, object]] = []
        for index, line in enumerate(payload.splitlines()):
            if index >= MAX_RECURSIVE_RECORDS:
                raise RecursiveOrchestrationError("recursive ledger record limit exceeded")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise RecursiveOrchestrationError(
                    "recursive ledger contains invalid JSON"
                ) from error
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != RECURSIVE_ORCHESTRATION_SCHEMA_VERSION
            ):
                raise RecursiveOrchestrationError("recursive ledger schema is unsupported")
            if record.get("sequence") != index:
                raise RecursiveOrchestrationError("recursive ledger sequence is not contiguous")
            records.append(record)
        return records

    def _append(self, value: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        records = self._read()
        payload = dict(value)
        payload.update(
            {
                "record_type": "recursive_orchestration_event",
                "schema_version": RECURSIVE_ORCHESTRATION_SCHEMA_VERSION,
                "sequence": len(records),
                "recorded_at": self._clock(),
            }
        )
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_RECURSIVE_RECORD_BYTES:
            raise RecursiveOrchestrationError("recursive ledger record is oversized")
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            import os

            os.fsync(handle.fileno())


def _policy_mapping(policy: RecursivePolicy) -> dict[str, object]:
    return {
        "max_depth": policy.max_depth,
        "max_nodes": policy.max_nodes,
        "allow_nested_teams": policy.allow_nested_teams,
        "allow_child_delegation": policy.allow_child_delegation,
    }


def _policy_from_mapping(value: object) -> RecursivePolicy:
    if not isinstance(value, dict):
        raise RecursiveOrchestrationError("recursive policy is invalid")
    try:
        return RecursivePolicy(
            **_policy_mapping(
                RecursivePolicy(
                    max_depth=value["max_depth"],
                    max_nodes=value["max_nodes"],
                    allow_nested_teams=value["allow_nested_teams"],
                    allow_child_delegation=value["allow_child_delegation"],
                )
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RecursiveOrchestrationError("recursive policy is invalid") from error


def _node_mapping(node: RecursiveNode) -> dict[str, object]:
    return {
        key: getattr(node, key)
        for key in (
            "node_id",
            "root_node_id",
            "parent_node_id",
            "depth",
            "kind",
            "status",
            "permission_mode",
            "capability",
            "objective_sha256",
            "created_at",
            "updated_at",
            "child_run_id",
            "team_id",
            "team_assignment_id",
            "task_id",
        )
    }


def _node_from_mapping(value: object) -> RecursiveNode:
    if not isinstance(value, dict):
        raise RecursiveOrchestrationError("recursive node record is invalid")
    try:
        return RecursiveNode(
            kind=RecursiveNodeKind(value["kind"]),
            status=RecursiveNodeStatus(value["status"]),
            **{
                key: value[key]
                for key in (
                    "node_id",
                    "root_node_id",
                    "parent_node_id",
                    "depth",
                    "permission_mode",
                    "capability",
                    "objective_sha256",
                    "created_at",
                    "updated_at",
                )
            },
            child_run_id=value.get("child_run_id"),
            team_id=value.get("team_id"),
            team_assignment_id=value.get("team_assignment_id"),
            task_id=value.get("task_id"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RecursiveOrchestrationError("recursive node record is invalid") from error


def _replace_node(node: RecursiveNode, **changes: object) -> RecursiveNode:
    values = _node_mapping(node)
    values.update(changes)
    return _node_from_mapping(values)


__all__ = [
    "MAX_RECURSIVE_DEPTH",
    "MAX_RECURSIVE_NODES",
    "RecursiveNode",
    "RecursiveNodeKind",
    "RecursiveNodeStatus",
    "RecursiveOrchestrationError",
    "RecursiveOrchestrationStore",
    "RecursivePolicy",
    "RecursiveTree",
]
