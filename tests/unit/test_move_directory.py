from pathlib import Path

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.move_directory import (
    MOVE_DIRECTORY_TOOL_NAME,
    MoveDirectoryOutcome,
    MoveDirectoryPreparationError,
    MoveDirectoryTool,
)


def request(source: str = "source", destination: str = "target") -> ToolUse:
    return ToolUse(
        "toolu_move_directory",
        MOVE_DIRECTORY_TOOL_NAME,
        ToolArguments.from_mapping({"destination": destination, "source": source}),
    )


def test_move_directory_moves_nonempty_tree_without_replacement(tmp_path: Path) -> None:
    (tmp_path / "source" / "nested").mkdir(parents=True)
    (tmp_path / "source" / "nested" / "file.txt").write_text("content", encoding="utf-8")
    tool = MoveDirectoryTool(tmp_path)

    result = tool.execute_detailed(tool.prepare(request()))

    assert result.outcome is MoveDirectoryOutcome.SUCCEEDED
    assert not (tmp_path / "source").exists()
    assert (tmp_path / "target" / "nested" / "file.txt").read_text() == "content"


def test_move_directory_rejects_descendant_existing_and_stale_destination(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    tool = MoveDirectoryTool(tmp_path)
    try:
        tool.prepare(request("source", "source/child"))
    except MoveDirectoryPreparationError as error:
        assert "inside source" in str(error)
    else:
        raise AssertionError("descendant destination must fail")

    (tmp_path / "target").mkdir()
    try:
        tool.prepare(request())
    except MoveDirectoryPreparationError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("existing destination must fail")

    (tmp_path / "target").rmdir()
    prepared = tool.prepare(request())
    (tmp_path / "target").mkdir()
    stale = tool.execute_detailed(prepared)
    assert stale.outcome is MoveDirectoryOutcome.FAILED
    assert "changed" in stale.tool_result.content
