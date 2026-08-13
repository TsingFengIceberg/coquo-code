from __future__ import annotations

import pytest

from coquo.agent.loop import AgentLoop
from coquo.agent.runtime import AgentRuntime, AgentTurnRequest
from coquo.core.contracts import AssistantText
from coquo.providers.fake import ScriptedFakeProvider
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.list_directory import ListDirectoryTool
from coquo.tools.read_file import ReadFileTool


def make_runtime(tmp_path, *, commit_turn=None) -> AgentRuntime:
    loop = AgentLoop(
        ScriptedFakeProvider([AssistantText("reply")]),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=commit_turn or (lambda _turn: None),
    )
    return AgentRuntime(loop)


def test_runtime_rejects_reentry_and_clears_after_success(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    prepared = runtime.prepare_turn(AgentTurnRequest("hello"))
    assert runtime.turn_state.active
    with pytest.raises(RuntimeError, match="already has an active turn"):
        runtime.prepare_turn(AgentTurnRequest("nested"))
    assert runtime.run_prepared(prepared, provider=ScriptedFakeProvider([AssistantText("reply")])) == "reply"
    assert not runtime.turn_state.active
    assert runtime.turn_state.action_lease is None
    assert runtime.turn_state.provider_runtime is None


def test_runtime_clears_after_commit_failure(tmp_path) -> None:
    def fail_commit(_turn) -> None:
        raise RuntimeError("commit failed")

    runtime = make_runtime(tmp_path, commit_turn=fail_commit)
    prepared = runtime.prepare_turn(AgentTurnRequest("hello"))
    with pytest.raises(RuntimeError, match="commit failed"):
        runtime.run_prepared(prepared, provider=ScriptedFakeProvider([AssistantText("reply")]))
    assert not runtime.turn_state.active
    assert runtime.turn_state.cancellation is None
    assert runtime.turn_state.event_sink is None
