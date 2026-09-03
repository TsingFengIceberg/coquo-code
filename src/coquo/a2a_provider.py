"""Standard A2A Provider boundary for a locally hosted Coquo session.

The Provider intentionally exposes only AgentCard, Message, Task, Artifact,
streaming, and cancellation semantics. A caller never receives the Host's
provider route, Session ID, transcript, tool calls, memory, approval details,
or internal orchestration state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

from a2a.server.events import Event
from a2a.server.request_handlers import RequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from coquo import __version__
from coquo.core.cancellation import TurnCancellation, TurnCancelled
from coquo.core.permissions import ApprovalMode, PermissionMode
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session import ProjectSession

_TERMINAL = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}
_MAX_PROMPT_CHARS = 256_000
_MAX_OUTPUT_CHARS = 1_000_000

SessionFactory = Callable[[Path], object]

MAX_A2A_LEDGER_BYTES = 32 * 1024 * 1024
MAX_A2A_LEDGER_EVENTS = 20_000


class A2AStoreError(RuntimeError):
    """Raised when the provider task ledger cannot be safely replayed."""


class A2ATaskStore:
    """Workspace-bound task event store used by the A2A boundary.

    The public A2A Task is the source of truth for this boundary.  Internal
    Coquo Session transcripts remain private and are never copied here.  A
    process restart replays terminal tasks and marks in-flight tasks as
    ``recovery-required`` through the protocol's failed state.
    """

    def __init__(self, workspace: Path) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise A2AStoreError("A2A workspace is not a directory")
        self.path = root / ".coquo" / "a2a" / "v1" / "tasks.jsonl"
        self.lock_path = self.path.with_suffix(".lock")
        self._guard = asyncio.Lock()

    async def append(
        self,
        kind: str,
        task: Task,
        *,
        owner: str,
        tenant: str,
        request_digest: str | None = None,
    ) -> None:
        if kind not in {"accepted", "status", "artifact", "recovered"}:
            raise A2AStoreError("A2A task event is invalid")
        mapping = MessageToDict(task, preserving_proto_field_name=True)
        event: dict[str, object] = {
            "schema_version": 1,
            "kind": kind,
            "task": mapping,
            "owner": owner,
            "tenant": tenant,
        }
        if request_digest is not None:
            event["request_digest"] = request_digest
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
        async with self._guard:
            self._append_sync(encoded)

    async def replay(self) -> tuple[tuple[Task, str, str, str | None], ...]:
        async with self._guard:
            return self._replay_sync()

    def _append_sync(self, encoded: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_file(descriptor)
            existing = self.path.stat().st_size if self.path.exists() else 0
            if existing + len(encoded) > MAX_A2A_LEDGER_BYTES:
                raise A2AStoreError("A2A task ledger exceeds size limit")
            with self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            _unlock_file(descriptor)
            os.close(descriptor)

    def _replay_sync(self) -> tuple[tuple[Task, str, str, str | None], ...]:
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_A2A_LEDGER_BYTES:
                raise ValueError
            latest: dict[tuple[str, str, str], tuple[Task, str, str, str | None]] = {}
            count = 0
            for line in raw.splitlines():
                count += 1
                if count > MAX_A2A_LEDGER_EVENTS:
                    raise ValueError
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or set(value) not in (
                    {"schema_version", "kind", "task", "owner", "tenant"},
                    {"schema_version", "kind", "task", "owner", "tenant", "request_digest"},
                ):
                    raise ValueError
                if value["schema_version"] != 1 or not isinstance(value["kind"], str):
                    raise ValueError
                task_value = value["task"]
                if not isinstance(task_value, dict):
                    raise ValueError
                task = Task()
                ParseDict(task_value, task)
                owner = value["owner"]
                tenant = value["tenant"]
                if not isinstance(owner, str) or not isinstance(tenant, str):
                    raise ValueError
                key = (owner, tenant, task.id)
                prior = latest.get(key)
                digest = (
                    value.get("request_digest")
                    if isinstance(value.get("request_digest"), str)
                    else (prior[1] if prior else None)
                )
                latest[key] = (task, owner, tenant, digest)
            return tuple(latest.values())
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            raise A2AStoreError("A2A task ledger is invalid") from None


def _lock_file(descriptor: int) -> None:
    if os.name == "nt":
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_file(descriptor: int) -> None:
    if os.name == "nt":
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@dataclass
class _TaskBinding:
    task: Task
    session: object
    cancellation: TurnCancellation
    owner: str
    tenant: str
    request_digest: str | None = None
    runner: asyncio.Task[None] | None = None
    subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)


def create_project_session_factory(
    *,
    profile: str | None = None,
    model: str | None = None,
    fixture_provider: bool = False,
) -> SessionFactory:
    """Create a fresh, provider-owned Session factory for one A2A process.

    A real model route is selected only through the same named-profile or
    explicit-model choices used by the local CLI.  The deterministic fake is
    deliberately opt-in so no caller can mistake fixture output for evidence
    of a model-backed Provider run.
    """
    selected_profile = profile.strip() if profile and profile.strip() else None
    selected_model = model.strip() if model and model.strip() else None

    def factory(workspace: Path) -> object:
        arguments: dict[str, object] = {
            "profile": selected_profile,
            "model": selected_model,
            "permission_mode": PermissionMode.READ_ONLY,
            "approval_mode": ApprovalMode.ASK,
        }
        if fixture_provider:
            arguments["fake_provider_factory"] = ScriptedFakeProvider
        return ProjectSession.open(workspace, **arguments)

    return factory


def build_agent_card(base_url: str) -> AgentCard:
    """Build the public, provider-opaque Card for the coding assistance skill."""
    base_url = base_url.rstrip("/")
    return AgentCard(
        name="Coquo Code Agent",
        description=(
            "A black-box, local coding-assistance Agent. The A2A boundary exposes "
            "only Task status and final result Artifacts; Host internals remain private."
        ),
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/a2a",
                protocol_binding="JSON_RPC",
                protocol_version="1.0",
            )
        ],
        provider=AgentProvider(organization="Coquo", url=base_url),
        version=__version__,
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text", "application/json"],
        default_output_modes=["text/markdown", "application/json"],
        skills=[
            AgentSkill(
                id="coding-assistance",
                name="Coding assistance",
                description=(
                    "Analyze a coding request in a Host-enforced read-only local workspace "
                    "and return a Markdown result plus structured completion metadata."
                ),
                tags=["coding", "analysis", "read-only"],
                examples=["Explain the architecture and likely change points for this repository."],
                input_modes=["text", "application/json"],
                output_modes=["text/markdown", "application/json"],
            )
        ],
    )


class CoquoA2AHandler(RequestHandler):
    """Bridge public A2A Tasks to one private, read-only ProjectSession each."""

    def __init__(
        self,
        workspace: Path,
        *,
        session_factory: SessionFactory | None = None,
        bearer_token: str | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve(strict=True)
        if not self._workspace.is_dir():
            raise ValueError("A2A workspace is not a directory")
        self._session_factory = session_factory or self._default_session_factory
        self._tasks: dict[tuple[str, str, str], _TaskBinding] = {}
        self._lock = asyncio.Lock()
        self._store = A2ATaskStore(self._workspace)
        self._bearer_token = bearer_token
        if bearer_token is not None and (not bearer_token or len(bearer_token) > 4096):
            raise ValueError("A2A bearer token is invalid")

    async def restore(self) -> None:
        """Restore public task state after a process restart.

        A running local Session cannot safely be resumed by this network
        adapter.  In-flight work is therefore retained as a public failed task
        with a precise recovery-required message, never silently retried.
        """
        for task, owner, tenant, digest in await self._store.replay():
            if task.status.state not in _TERMINAL:
                task.status.CopyFrom(
                    TaskStatus(
                        state=TaskState.TASK_STATE_FAILED,
                        message=_agent_message(
                            "Task recovery required after A2A process restart",
                            task.context_id,
                            task.id,
                        ),
                    )
                )
                await self._store.append("recovered", task, owner=owner, tenant=tenant)
            self._tasks[self._key(task.id, owner, tenant)] = _TaskBinding(
                task=task,
                session=None,
                cancellation=TurnCancellation(),
                owner=owner,
                tenant=tenant,
                request_digest=digest,
            )

    def _default_session_factory(self, workspace: Path) -> object:
        # Never inherit an interactive CLI's permission elevation. This network
        # boundary starts a fresh Provider-owned Session with a read-only ceiling
        # and no automatic approval of any side effect.
        return ProjectSession.open(
            workspace,
            permission_mode=PermissionMode.READ_ONLY,
            approval_mode=ApprovalMode.ASK,
        )

    @staticmethod
    def _scope(context: object) -> tuple[str, str]:
        state = getattr(context, "state", {})
        if not isinstance(state, dict):
            state = {}
        owner = str(state.get("a2a_owner") or "anonymous")
        tenant = str(getattr(context, "tenant", "") or "")
        return owner, tenant

    @staticmethod
    def _key(task_id: str, owner: str, tenant: str) -> tuple[str, str, str]:
        return owner, tenant, task_id

    async def on_message_send(self, params: SendMessageRequest, context) -> Task | Message:
        binding = await self._accept(params, context)
        return binding.task

    async def on_message_send_stream(
        self, params: SendMessageRequest, context
    ) -> AsyncGenerator[Event]:
        binding = await self._accept(params, context)
        yield binding.task
        async for event in self._subscribe(binding):
            yield event

    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context
    ) -> AsyncGenerator[Event]:
        owner, tenant = self._scope(context)
        async with self._lock:
            binding = self._tasks.get(self._key(params.id, owner, tenant))
        if binding is None:
            raise TaskNotFoundError
        yield binding.task
        async for event in self._subscribe(binding):
            yield event

    async def on_get_task(self, params: GetTaskRequest, context) -> Task | None:
        owner, tenant = self._scope(context)
        async with self._lock:
            binding = self._tasks.get(self._key(params.id, owner, tenant))
        if binding is None:
            raise TaskNotFoundError
        return binding.task

    async def on_list_tasks(self, params: ListTasksRequest, context) -> ListTasksResponse:
        owner, tenant = self._scope(context)
        async with self._lock:
            tasks = [
                binding.task
                for (candidate_owner, candidate_tenant, _), binding in self._tasks.items()
                if candidate_owner == owner
                and candidate_tenant == tenant
                and (not params.context_id or binding.task.context_id == params.context_id)
            ]
        if params.status:
            tasks = [task for task in tasks if task.status.state == params.status]
        page_size = max(1, min(int(params.page_size or 50), 100))
        tasks = tasks[:page_size]
        return ListTasksResponse(tasks=tasks, page_size=len(tasks), total_size=len(tasks))

    async def on_cancel_task(self, params: CancelTaskRequest, context) -> Task | None:
        owner, tenant = self._scope(context)
        async with self._lock:
            binding = self._tasks.get(self._key(params.id, owner, tenant))
        if binding is None:
            raise TaskNotFoundError
        if binding.task.status.state in _TERMINAL:
            return binding.task
        binding.cancellation.request()
        await self._publish_status(
            binding, TaskState.TASK_STATE_CANCELED, "Task canceled by caller"
        )
        return binding.task

    async def on_create_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError(message="Push notifications are not supported")

    async def on_get_task_push_notification_config(
        self, params: GetTaskPushNotificationConfigRequest, context
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError(message="Push notifications are not supported")

    async def on_list_task_push_notification_configs(
        self, params: ListTaskPushNotificationConfigsRequest, context
    ) -> ListTaskPushNotificationConfigsResponse:
        raise UnsupportedOperationError(message="Push notifications are not supported")

    async def on_delete_task_push_notification_config(
        self, params: DeleteTaskPushNotificationConfigRequest, context
    ) -> None:
        raise UnsupportedOperationError(message="Push notifications are not supported")

    async def on_get_extended_agent_card(
        self, params: GetExtendedAgentCardRequest, context
    ) -> AgentCard:
        raise UnsupportedOperationError(message="Extended AgentCard is not supported")

    async def _accept(self, params: SendMessageRequest, context: object) -> _TaskBinding:
        owner, tenant = self._scope(context)
        message = Message()
        message.CopyFrom(params.message)
        if not message.message_id:
            message.message_id = f"msg-{uuid4().hex}"
        request_digest = _message_request_digest(message)
        async with self._lock:
            for (candidate_owner, candidate_tenant, _), candidate in self._tasks.items():
                if candidate_owner != owner or candidate_tenant != tenant:
                    continue
                if (
                    candidate.task.history
                    and candidate.task.history[0].message_id == message.message_id
                ):
                    if (
                        candidate.request_digest is not None
                        and candidate.request_digest != request_digest
                    ):
                        raise ValueError("A2A message_id was reused with different message content")
                    return candidate
        task_id = message.task_id or f"task-{uuid4().hex}"
        context_id = message.context_id or f"ctx-{uuid4().hex}"
        message.task_id = task_id
        message.context_id = context_id
        key = self._key(task_id, owner, tenant)
        async with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                if existing.task.context_id != context_id:
                    raise ValueError("Message context_id does not match the existing Task")
                if existing.task.status.state in _TERMINAL:
                    return existing
                raise ValueError("Coquo A2A Tasks do not accept continuation input")
            prompt = _prompt_from_message(message)
            session = self._session_factory(self._workspace)
            binding = _TaskBinding(
                task=Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[message],
                ),
                session=session,
                cancellation=TurnCancellation(),
                owner=owner,
                tenant=tenant,
                request_digest=request_digest,
            )
            self._tasks[key] = binding
            await self._store.append(
                "accepted",
                binding.task,
                owner=owner,
                tenant=tenant,
                request_digest=request_digest,
            )
            binding.runner = asyncio.create_task(self._run(binding, prompt))
            return binding

    async def _run(self, binding: _TaskBinding, prompt: str) -> None:
        try:
            await self._publish_status(
                binding, TaskState.TASK_STATE_WORKING, "Coding assistance is running"
            )
            result = await _run_in_daemon_thread(
                _run_session_prompt,
                binding.session,
                prompt,
                binding.cancellation,
            )
            if binding.task.status.state == TaskState.TASK_STATE_CANCELED:
                return
            result = result[:_MAX_OUTPUT_CHARS]
            artifact = Artifact(
                artifact_id="coding-result",
                name="Coding assistance result",
                description="Provider-owned coding result without Host-private execution details.",
                parts=[
                    Part(text=result, media_type="text/markdown"),
                    Part(data=_completion_data_value(result), media_type="application/json"),
                ],
            )
            binding.task.artifacts.append(artifact)
            await self._store.append(
                "artifact", binding.task, owner=binding.owner, tenant=binding.tenant
            )
            await self._emit(
                binding,
                TaskArtifactUpdateEvent(
                    task_id=binding.task.id,
                    context_id=binding.task.context_id,
                    artifact=artifact,
                    last_chunk=True,
                ),
            )
            await self._publish_status(
                binding, TaskState.TASK_STATE_COMPLETED, "Coding assistance completed"
            )
        except TurnCancelled:
            if binding.task.status.state not in _TERMINAL:
                await self._publish_status(
                    binding, TaskState.TASK_STATE_CANCELED, "Task canceled by caller"
                )
        except Exception:
            if binding.task.status.state not in _TERMINAL:
                await self._publish_status(
                    binding, TaskState.TASK_STATE_FAILED, "Coding assistance failed"
                )
        finally:
            close = getattr(binding.session, "close", None)
            if callable(close):
                await _run_in_daemon_thread(close)

    async def _publish_status(self, binding: _TaskBinding, state: int, text: str) -> None:
        if binding.task.status.state in _TERMINAL:
            return
        binding.task.status.CopyFrom(
            TaskStatus(
                state=state, message=_agent_message(text, binding.task.context_id, binding.task.id)
            )
        )
        await self._store.append("status", binding.task, owner=binding.owner, tenant=binding.tenant)
        await self._emit(
            binding,
            TaskStatusUpdateEvent(
                task_id=binding.task.id,
                context_id=binding.task.context_id,
                status=binding.task.status,
            ),
        )

    async def _emit(self, binding: _TaskBinding, event: Event) -> None:
        for queue in tuple(binding.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)

    async def _subscribe(self, binding: _TaskBinding) -> AsyncGenerator[Event]:
        if binding.task.status.state in _TERMINAL:
            return
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=32)
        binding.subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, TaskStatusUpdateEvent) and event.status.state in _TERMINAL:
                    return
        finally:
            binding.subscribers.discard(queue)


def _run_session_prompt(session: object, prompt: str, cancellation: TurnCancellation) -> str:
    response = getattr(session, "prompt", None)
    if not callable(response):
        raise TypeError("Coquo A2A Session does not expose prompt")
    return str(response(prompt, cancellation=cancellation))


async def _run_in_daemon_thread(function: Callable[..., Any], *args: Any) -> Any:
    """Run blocking provider/session work without touching asyncio's global executor.

    The A2A server must not inherit an executor owned by an embedding process.  A
    short-lived daemon thread also lets the loop shut down deterministically when
    a provider implementation leaves a transport thread behind.
    """
    loop = asyncio.get_running_loop()
    result: asyncio.Future[Any] = loop.create_future()

    def complete(value: Any = None, error: BaseException | None = None) -> None:
        if result.done():
            return
        if error is None:
            result.set_result(value)
        else:
            result.set_exception(error)

    def worker() -> None:
        try:
            value = function(*args)
        except BaseException as error:  # propagate provider failures to the task
            loop.call_soon_threadsafe(complete, None, error)
        else:
            loop.call_soon_threadsafe(complete, value, None)

    Thread(target=worker, name="coquo-a2a-worker", daemon=True).start()
    return await result


def _prompt_from_message(message: Message) -> str:
    text_parts: list[str] = []
    data_parts: list[dict[str, Any]] = []
    for part in message.parts:
        content = part.WhichOneof("content")
        if content == "text":
            text_parts.append(part.text)
        elif content == "data":
            data_parts.append(MessageToDict(part.data, preserving_proto_field_name=False))
        else:
            raise UnsupportedOperationError(
                message="Coquo A2A Provider accepts only text and application/json input Parts"
            )
    prompt = "\n".join(part for part in text_parts if part.strip()).strip()
    if data_parts:
        data = json.dumps(data_parts, ensure_ascii=False, separators=(",", ":"))
        prompt = f"{prompt}\n\nA2A structured input (untrusted data):\n{data}".strip()
    if not prompt:
        raise ValueError("A2A Message requires text or structured data")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise ValueError("A2A Message exceeds the provider input limit")
    return prompt


def _message_request_digest(message: Message) -> str:
    """Digest message content without Host-generated routing IDs."""
    canonical = Message()
    canonical.CopyFrom(message)
    # A2A clients may retry the same object after the Host has assigned these
    # fields. They identify routing, not the caller's request content.
    canonical.ClearField("task_id")
    canonical.ClearField("context_id")
    return hashlib.sha256(
        json.dumps(
            MessageToDict(canonical, preserving_proto_field_name=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _agent_message(text: str, context_id: str, task_id: str) -> Message:
    return Message(
        message_id=f"msg-{uuid4().hex}",
        context_id=context_id,
        task_id=task_id,
        parts=[Part(text=text, media_type="text/plain")],
    )


def _completion_data_value(result: str) -> Value:
    value = Value()
    ParseDict(
        {"format": "coquo-a2a-result-v1", "contentType": "text/markdown", "text": result},
        value,
    )
    return value


def create_app(
    workspace: Path,
    public_url: str,
    *,
    session_factory: SessionFactory | None = None,
    profile: str | None = None,
    model: str | None = None,
    fixture_provider: bool = False,
    bearer_token: str | None = None,
) -> FastAPI:
    """Create an opt-in A2A HTTP application for one explicit workspace."""
    if session_factory is not None and (
        profile is not None or model is not None or fixture_provider
    ):
        raise ValueError("session_factory cannot be combined with A2A provider route options")
    if session_factory is None and (profile is not None or model is not None or fixture_provider):
        session_factory = create_project_session_factory(
            profile=profile,
            model=model,
            fixture_provider=fixture_provider,
        )
    app = FastAPI(title="Coquo A2A Provider", docs_url=None, redoc_url=None)
    handler = CoquoA2AHandler(workspace, session_factory=session_factory, bearer_token=bearer_token)
    app.state.coquo_a2a_handler = handler

    @app.exception_handler(A2AStoreError)
    async def a2a_store_error(_request: Request, error: A2AStoreError):
        return JSONResponse(
            {"error": "a2a_store_unavailable", "message": str(error)}, status_code=503
        )

    @app.on_event("startup")
    async def _restore_a2a_tasks() -> None:
        await handler.restore()

    @app.middleware("http")
    async def _authenticate_a2a(request: Request, call_next):
        # AgentCard stays discoverable.  When configured, every task route
        # requires an exact Bearer token without exposing the token to A2A
        # task state or model prompts.
        if bearer_token is not None and request.url.path.startswith("/a2a"):
            header = request.headers.get("authorization", "")
            expected = f"Bearer {bearer_token}"
            if not hmac.compare_digest(header, expected):
                return JSONResponse({"error": "A2A authentication required"}, status_code=401)
        return await call_next(request)

    card = build_agent_card(public_url)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
        rest_routes=create_rest_routes(handler, path_prefix="/a2a"),
    )
    return app


__all__ = ["CoquoA2AHandler", "build_agent_card", "create_app"]
