from __future__ import annotations

import json

import pytest

from coquo.agent.loop import AgentLoop
from coquo.core.compaction import EffectiveContextSummary
from coquo.core.contracts import (
    AssistantText,
    ToolArguments,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.providers.fake import ScriptedFakeProvider
from coquo.skills import SkillInventoryLoader, SkillSourceKind
from coquo.skills import import_skill, verify_skill_lock
from coquo.skills.catalog import MAX_SKILL_RESOURCE_BYTES, SkillCatalogError
from coquo.core.extensions import ToolExecutionKind
from coquo.core.permissions import PermissionAction
from coquo.tools.catalog import TOOL_REGISTRY_SNAPSHOT
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.list_directory import ListDirectoryTool
from coquo.tools.read_file import ReadFileTool


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
        skill_resource_reader=loader.read_resource,
    )


def test_inventory_uses_exact_source_priority_and_keeps_shadowed_candidates(tmp_path) -> None:
    config = tmp_path / "config"
    write_skill(config / "coquo" / "skills", "release", body="User body.\n")
    write_skill(tmp_path / ".agents" / "skills", "release", body="Project body.\n")
    write_skill(tmp_path / ".coquo" / "skills", "release", body="Local body.\n")

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


def test_malicious_yaml_tag_is_data_error_and_never_constructed(tmp_path) -> None:
    package = tmp_path / ".agents" / "skills" / "unsafe"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "manifest-version: 1\n"
        "name: unsafe\n"
        "description: !!python/object/apply:os.system ['touch SHOULD_NOT_EXIST']\n"
        "---\n"
        "Never run this.\n",
        encoding="utf-8",
    )

    inventory = SkillInventoryLoader(tmp_path, {}).load()

    assert inventory.active == ()
    assert [issue.code for issue in inventory.issues] == ["invalid-yaml"]
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


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
    assert {
        "skill_search",
        "skill_load",
        "skill_read_resource",
        "tool_search",
        "tool_promote",
    } <= final_names

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


def test_inventory_indexes_nested_text_and_binary_resources_without_following_symlinks(
    tmp_path,
) -> None:
    root = tmp_path / ".agents" / "skills"
    write_skill(root, "resourceful")
    package = root / "resourceful"
    (package / "references").mkdir()
    (package / "references" / "guide.md").write_text("Use this guide.\n", encoding="utf-8")
    (package / "fixture.bin").write_bytes(b"\xff\x00")

    inventory = SkillInventoryLoader(tmp_path, {}).load()
    candidate = inventory.get("resourceful")

    assert [resource.path for resource in candidate.resources] == [
        "fixture.bin",
        "references/guide.md",
    ]
    assert [resource.text_readable for resource in candidate.resources] == [False, True]
    assert inventory.snapshot_id.startswith("skills-v2-")

    (package / "linked.md").symlink_to(package / "references" / "guide.md")
    rejected = SkillInventoryLoader(tmp_path, {}).load()
    assert rejected.active == ()
    assert [issue.code for issue in rejected.issues] == ["resource-symlink"]


def test_import_rejects_symlink_and_oversized_resource_without_target_or_lock(tmp_path) -> None:
    source_root = tmp_path / "sources"
    write_skill(source_root, "linked")
    linked = source_root / "linked"
    (linked / "target.txt").write_text("target\n", encoding="utf-8")
    (linked / "link.txt").symlink_to(linked / "target.txt")

    with pytest.raises(SkillCatalogError, match="symlinks"):
        import_skill(tmp_path, linked, environment={})
    assert not (tmp_path / ".agents" / "skills" / "linked").exists()
    assert not (tmp_path / ".agents" / "skill-locks" / "linked.json").exists()

    write_skill(source_root, "huge")
    (source_root / "huge" / "large.bin").write_bytes(b"x" * (MAX_SKILL_RESOURCE_BYTES + 1))
    with pytest.raises(SkillCatalogError, match="exceeds"):
        import_skill(tmp_path, source_root / "huge", environment={})
    assert not (tmp_path / ".agents" / "skills" / "huge").exists()


def test_import_detects_source_drift_and_removes_its_new_target(tmp_path, monkeypatch) -> None:
    from coquo.skills import authoring

    source_root = tmp_path / "sources"
    write_skill(source_root, "drifting")
    resource = source_root / "drifting" / "guide.md"
    resource.write_text("before\n", encoding="utf-8")
    original = authoring.read_skill_package_file
    calls = 0

    def drifting_read(package, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            resource.write_text("after\n", encoding="utf-8")
        return original(package, path)

    monkeypatch.setattr(authoring, "read_skill_package_file", drifting_read)

    with pytest.raises(SkillCatalogError, match="changed during import"):
        import_skill(tmp_path, source_root / "drifting", environment={})
    assert not (tmp_path / ".agents" / "skills" / "drifting").exists()
    assert not (tmp_path / ".agents" / "skill-locks" / "drifting.json").exists()


def test_import_detects_target_directory_replacement_without_deleting_replacement(
    tmp_path, monkeypatch
) -> None:
    from coquo.skills import authoring

    source_root = tmp_path / "sources"
    source = source_root / "replaced"
    write_skill(source_root, "replaced")
    original = authoring._copy_package_file

    def replace_after_copy(source_package, target_package, relative):
        created = original(source_package, target_package, relative)
        orphan = target_package.with_name("replaced-original")
        target_package.rename(orphan)
        target_package.mkdir()
        (target_package / "SKILL.md").write_bytes((orphan / "SKILL.md").read_bytes())
        return created

    monkeypatch.setattr(authoring, "_copy_package_file", replace_after_copy)

    with pytest.raises(SkillCatalogError, match="target changed"):
        import_skill(tmp_path, source, environment={})
    replacement = tmp_path / ".agents" / "skills" / "replaced"
    assert replacement.exists()
    assert not (tmp_path / ".agents" / "skill-locks" / "replaced.json").exists()


def test_import_lock_fails_closed_when_tampered(tmp_path) -> None:
    source_root = tmp_path / "sources"
    write_skill(source_root, "locked")
    import_skill(tmp_path, source_root / "locked", environment={})
    lock = tmp_path / ".agents" / "skill-locks" / "locked.json"
    lock.write_text('{"lock-version":1}\n', encoding="utf-8")

    with pytest.raises(SkillCatalogError, match="fields are invalid"):
        verify_skill_lock(tmp_path, "locked", environment={})


def test_skill_scripts_are_resources_and_have_no_direct_execution_contract() -> None:
    contracts = {contract.name: contract for contract in TOOL_REGISTRY_SNAPSHOT.contracts}

    assert "skill_run_script" not in contracts
    assert contracts["skill_read_resource"].execution_kind is ToolExecutionKind.TOOL_DISCOVERY
    assert contracts["skill_read_resource"].permission_actions == ()
    assert contracts["run_command"].execution_kind is ToolExecutionKind.HOST_ACTION
    assert contracts["run_command"].permission_actions == (PermissionAction.DANGEROUS,)


def test_active_skill_can_read_one_exact_text_resource_and_rejects_binary(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    write_skill(root, "resourceful", body="Read the selected reference.\n")
    package = root / "resourceful"
    (package / "references").mkdir()
    (package / "references" / "guide.md").write_text("Checklist item.\n", encoding="utf-8")
    (package / "fixture.bin").write_bytes(b"\xff")
    loader = SkillInventoryLoader(tmp_path, {})
    candidate = loader.load().get("resourceful")
    manifest = candidate.manifest
    text_resource = next(item for item in candidate.resources if item.text_readable)
    binary_resource = next(item for item in candidate.resources if not item.text_readable)
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "search",
                "skill_search",
                ToolArguments.from_mapping({"query": "resourceful", "max_results": 1}),
            ),
            ToolUse(
                "load",
                "skill_load",
                ToolArguments.from_mapping(
                    {"name": "resourceful", "fingerprint": manifest.fingerprint}
                ),
            ),
            ToolUse(
                "read-text",
                "skill_read_resource",
                ToolArguments.from_mapping(
                    {
                        "name": "resourceful",
                        "skill_fingerprint": manifest.fingerprint,
                        "path": text_resource.path,
                        "resource_fingerprint": text_resource.fingerprint,
                    }
                ),
            ),
            ToolUse(
                "read-binary",
                "skill_read_resource",
                ToolArguments.from_mapping(
                    {
                        "name": "resourceful",
                        "skill_fingerprint": manifest.fingerprint,
                        "path": binary_resource.path,
                        "resource_fingerprint": binary_resource.fingerprint,
                    }
                ),
            ),
            AssistantText("done"),
        ]
    )
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("use the resource") == "done"
    text_result = loop.history[-4]
    binary_result = loop.history[-2]
    assert isinstance(text_result, ToolResult)
    assert json.loads(text_result.content)["content"] == "Checklist item.\n"
    assert isinstance(binary_result, ToolResult)
    assert binary_result.is_error
    assert binary_result.content == "Skill resource is not UTF-8 text"


def test_resource_read_rejects_inventory_drift_after_exact_load(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    write_skill(root, "resourceful")
    package = root / "resourceful"
    resource_path = package / "guide.md"
    resource_path.write_text("Version one.\n", encoding="utf-8")
    loader = SkillInventoryLoader(tmp_path, {})
    candidate = loader.load().get("resourceful")
    resource = candidate.resources[0]

    class DriftingResourceProvider(ScriptedFakeProvider):
        def respond(self, request):
            response = super().respond(request)
            if isinstance(response, ToolUse) and response.name == "skill_read_resource":
                resource_path.write_text("Version two.\n", encoding="utf-8")
            return response

    provider = DriftingResourceProvider(
        [
            ToolUse(
                "search",
                "skill_search",
                ToolArguments.from_mapping({"query": "resourceful", "max_results": 1}),
            ),
            ToolUse(
                "load",
                "skill_load",
                ToolArguments.from_mapping(
                    {
                        "name": "resourceful",
                        "fingerprint": candidate.manifest.fingerprint,
                    }
                ),
            ),
            ToolUse(
                "read",
                "skill_read_resource",
                ToolArguments.from_mapping(
                    {
                        "name": "resourceful",
                        "skill_fingerprint": candidate.manifest.fingerprint,
                        "path": resource.path,
                        "resource_fingerprint": resource.fingerprint,
                    }
                ),
            ),
            AssistantText("drift observed"),
        ]
    )
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("read it") == "drift observed"
    result = loop.history[-2]
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert result.content == "Skill inventory changed before resource reading"


def test_multiple_skills_intersect_action_tools_and_reject_duplicate_activation(
    tmp_path,
) -> None:
    root = tmp_path / ".agents" / "skills"
    write_skill(root, "reader", allowed="  - read_file\n  - glob\n")
    write_skill(root, "reviewer", allowed="  - read_file\n  - grep\n")
    loader = SkillInventoryLoader(tmp_path, {})
    reader = loader.load().get("reader").manifest
    reviewer = loader.load().get("reviewer").manifest
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "search-reader",
                "skill_search",
                ToolArguments.from_mapping({"query": "reader", "max_results": 1}),
            ),
            ToolUse(
                "load-reader",
                "skill_load",
                ToolArguments.from_mapping({"name": "reader", "fingerprint": reader.fingerprint}),
            ),
            ToolUse(
                "search-reviewer",
                "skill_search",
                ToolArguments.from_mapping({"query": "reviewer", "max_results": 1}),
            ),
            ToolUse(
                "load-reviewer",
                "skill_load",
                ToolArguments.from_mapping(
                    {"name": "reviewer", "fingerprint": reviewer.fingerprint}
                ),
            ),
            ToolUse(
                "search-reader-again",
                "skill_search",
                ToolArguments.from_mapping({"query": "reader", "max_results": 1}),
            ),
            ToolUse(
                "load-reader-again",
                "skill_load",
                ToolArguments.from_mapping({"name": "reader", "fingerprint": reader.fingerprint}),
            ),
            AssistantText("composed"),
        ]
    )
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("compose workflows") == "composed"
    final_names = {
        definition.name for definition in provider.received_requests[-1].tool_definitions
    }
    assert "read_file" in final_names
    assert "glob" not in final_names
    assert "grep" not in final_names
    duplicate = loop.history[-2]
    assert isinstance(duplicate, ToolResult)
    assert duplicate.is_error
    assert duplicate.content == "Skill is already active in Effective Context"
    loaded_payloads = [
        json.loads(item.content)
        for item in loop.history
        if isinstance(item, ToolResult)
        and not item.is_error
        and '"kind":"skill_loaded"' in item.content
    ]
    assert [payload["activation"]["active_count"] for payload in loaded_payloads] == [1, 2]
    assert loaded_payloads[-1]["activation"]["remaining_action_tools"] == ["read_file"]


def test_skill_load_limits_bound_one_turn_and_retained_active_set(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    for index in range(1, 6):
        write_skill(root, f"skill-{index}")
    loader = SkillInventoryLoader(tmp_path, {})
    manifests = [loader.load().get(f"skill-{index}").manifest for index in range(1, 6)]
    responses = []
    for index, manifest in enumerate(manifests, start=1):
        responses.extend(
            (
                ToolUse(
                    f"search-{index}",
                    "skill_search",
                    ToolArguments.from_mapping({"query": manifest.name, "max_results": 1}),
                ),
                ToolUse(
                    f"load-{index}",
                    "skill_load",
                    ToolArguments.from_mapping(
                        {"name": manifest.name, "fingerprint": manifest.fingerprint}
                    ),
                ),
            )
        )
    provider = ScriptedFakeProvider([*responses, AssistantText("bounded")])
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("load all") == "bounded"
    fifth_result = loop.history[-2]
    assert isinstance(fifth_result, ToolResult)
    assert fifth_result.is_error
    assert fifth_result.content == "Skill load limit reached: at most 4 per Turn"

    resumed_provider = ScriptedFakeProvider(
        [
            ToolUse(
                "search-fifth-again",
                "skill_search",
                ToolArguments.from_mapping({"query": "skill-5", "max_results": 1}),
            ),
            ToolUse(
                "load-fifth-again",
                "skill_load",
                ToolArguments.from_mapping(
                    {"name": "skill-5", "fingerprint": manifests[-1].fingerprint}
                ),
            ),
            AssistantText("active limit observed"),
        ]
    )
    resumed = loop_for(tmp_path, resumed_provider, loader, history=loop.history)

    assert resumed.run("try another") == "active limit observed"
    active_limit = resumed.history[-2]
    assert isinstance(active_limit, ToolResult)
    assert active_limit.is_error
    assert active_limit.content == "Active Skill limit reached: at most 4"


def test_skill_instruction_budget_rejects_cumulative_overflow(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    for name in ("large-one", "large-two", "large-three"):
        write_skill(root, name, body="x" * 22_000 + "\n")
    loader = SkillInventoryLoader(tmp_path, {})
    manifests = [
        loader.load().get(name).manifest for name in ("large-one", "large-two", "large-three")
    ]
    responses = []
    for index, manifest in enumerate(manifests, start=1):
        responses.extend(
            (
                ToolUse(
                    f"search-large-{index}",
                    "skill_search",
                    ToolArguments.from_mapping({"query": manifest.name, "max_results": 1}),
                ),
                ToolUse(
                    f"load-large-{index}",
                    "skill_load",
                    ToolArguments.from_mapping(
                        {"name": manifest.name, "fingerprint": manifest.fingerprint}
                    ),
                ),
            )
        )
    provider = ScriptedFakeProvider([*responses, AssistantText("bounded")])
    loop = loop_for(tmp_path, provider, loader)

    assert loop.run("load large workflows") == "bounded"
    result = loop.history[-2]
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert result.content == "Active Skill instruction byte limit would be exceeded"
