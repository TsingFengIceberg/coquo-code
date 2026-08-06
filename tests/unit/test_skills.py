from __future__ import annotations

import json

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.core.compaction import EffectiveContextSummary
from leonervis_code.core.contracts import (
    AssistantText,
    ToolArguments,
    ToolResult,
    ToolUse,
    UserMessage,
)
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.skills import SkillInventoryLoader, SkillSourceKind
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool


def write_skill(root, name: str, *, allowed: str = "  - read_file\n", body: str = "Read first.\n"):
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "manifest-version: 1\n"
        f"name: {name}\n"
        f"description: Workflow for {name}\n"
        "allowed-tools:\n"
        f"{allowed}"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )


def loop_for(
    tmp_path,
    provider,
    loader,
    *,
    history=(),
    effective_history=None,
    effective_summary=None,
    effective_source="full_committed_history",
):
    return AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        initial_history=history,
        initial_effective_history=effective_history,
        initial_effective_summary=effective_summary,
        initial_effective_source=effective_source,
        skill_inventory_factory=loader.load,
    )


def test_inventory_uses_exact_source_priority_and_keeps_shadowed_candidates(tmp_path) -> None:
    config = tmp_path / "config"
    write_skill(config / "leonervis-code" / "skills", "release", body="User body.\n")
    write_skill(tmp_path / ".agents" / "skills", "release", body="Project body.\n")
    write_skill(tmp_path / ".leonervis-code" / "skills", "release", body="Local body.\n")

    inventory = SkillInventoryLoader(tmp_path, {"XDG_CONFIG_HOME": str(config)}).load()

    assert [candidate.source for candidate in inventory.candidates] == [
        SkillSourceKind.WORKSPACE_LOCAL,
        SkillSourceKind.PROJECT_SHARED,
        SkillSourceKind.USER,
    ]
    assert inventory.get("release").manifest.instructions == "Local body.\n"
    assert [candidate.shadowed_by for candidate in inventory.candidates] == [
        None,
        SkillSourceKind.WORKSPACE_LOCAL,
        SkillSourceKind.WORKSPACE_LOCAL,
    ]
    assert not inventory.issues


def test_inventory_reports_invalid_yaml_symlink_and_crlf_without_loading_them(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    invalid = root / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: [\n---\nbody\n", encoding="utf-8")
    crlf = root / "crlf"
    crlf.mkdir()
    (crlf / "SKILL.md").write_bytes(
        b"---\r\nmanifest-version: 1\r\nname: crlf\r\ndescription: bad\r\n---\r\nbody\r\n"
    )
    target = root / "target"
    target.mkdir()
    (root / "linked").symlink_to(target, target_is_directory=True)

    inventory = SkillInventoryLoader(tmp_path, {}).load()

    assert inventory.active == ()
    assert {issue.code for issue in inventory.issues} == {
        "invalid-newline",
        "invalid-yaml",
        "invalid-package",
        "missing-manifest",
    }


def test_skill_search_load_and_action_restriction_persist_through_effective_history(
    tmp_path,
) -> None:
    write_skill(tmp_path / ".agents" / "skills", "review", body="Inspect before editing.\n")
    loader = SkillInventoryLoader(tmp_path, {})
    manifest = loader.load().get("review").manifest
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "search-skill",
                "skill_search",
                ToolArguments.from_mapping({"query": "review", "max_results": 4}),
            ),
            ToolUse(
                "load-skill",
                "skill_load",
                ToolArguments.from_mapping({"name": "review", "fingerprint": manifest.fingerprint}),
            ),
            AssistantText("loaded"),
        ]
    )
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("review this") == "loaded"
    loaded = json.loads(loop.history[-2].content)
    assert loaded["kind"] == "skill_loaded"
    assert loaded["instructions"] == "Inspect before editing.\n"
    final_names = {
        definition.name for definition in provider.received_requests[-1].tool_definitions
    }
    assert "read_file" in final_names
    assert "run_command" not in final_names
    assert {"skill_search", "skill_load", "tool_search", "tool_promote"} <= final_names

    next_provider = ScriptedFakeProvider([AssistantText("continued")])
    resumed = loop_for(tmp_path, next_provider, loader, history=loop.history)
    assert resumed.run("continue") == "continued"
    next_names = {
        definition.name for definition in next_provider.received_requests[0].tool_definitions
    }
    assert "read_file" in next_names
    assert "run_command" not in next_names


def test_skill_restriction_disappears_when_compaction_removes_exact_load_pair(tmp_path) -> None:
    write_skill(tmp_path / ".agents" / "skills", "review")
    loader = SkillInventoryLoader(tmp_path, {})
    manifest = loader.load().get("review").manifest
    payload = json.dumps(
        {
            "allowed_tools": ["read_file"],
            "fingerprint": manifest.fingerprint,
            "instructions": manifest.instructions,
            "kind": "skill_loaded",
            "name": "review",
            "source": "project-shared",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    full = (
        UserMessage("load"),
        ToolUse(
            "load-old",
            "skill_load",
            ToolArguments.from_mapping({"name": "review", "fingerprint": manifest.fingerprint}),
        ),
        ToolResult("load-old", payload),
        AssistantText("loaded"),
        UserMessage("later"),
        AssistantText("later done"),
    )
    provider = ScriptedFakeProvider([AssistantText("fresh")])
    loop = loop_for(
        tmp_path,
        provider,
        loader,
        history=full,
        effective_history=full[-2:],
        effective_summary=EffectiveContextSummary("A prior Skill was used."),
        effective_source="compact_checkpoint",
    )
    assert loop.run("fresh turn") == "fresh"
    assert "run_command" in {
        definition.name for definition in provider.received_requests[0].tool_definitions
    }


def test_skill_load_stale_rejects_inventory_change_after_search(tmp_path) -> None:
    write_skill(tmp_path / ".agents" / "skills", "review")
    loader = SkillInventoryLoader(tmp_path, {})
    manifest = loader.load().get("review").manifest

    class DriftingProvider(ScriptedFakeProvider):
        def respond(self, request):
            response = super().respond(request)
            if len(self.received_requests) == 2:
                path = tmp_path / ".agents" / "skills" / "review" / "SKILL.md"
                path.write_text(path.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")
            return response

    provider = DriftingProvider(
        [
            ToolUse(
                "search",
                "skill_search",
                ToolArguments.from_mapping({"query": "review", "max_results": 1}),
            ),
            ToolUse(
                "load",
                "skill_load",
                ToolArguments.from_mapping({"name": "review", "fingerprint": manifest.fingerprint}),
            ),
            AssistantText("stale observed"),
        ]
    )
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("review") == "stale observed"
    stale_result = loop.history[-2]
    assert isinstance(stale_result, ToolResult)
    assert stale_result.is_error
    assert stale_result.content == "Skill inventory changed after this Turn was prepared"
    assert "run_command" in {
        definition.name for definition in provider.received_requests[-1].tool_definitions
    }
