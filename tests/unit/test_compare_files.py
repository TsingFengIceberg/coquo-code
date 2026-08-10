from pathlib import Path

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.compare_files import COMPARE_FILES_TOOL_NAME, CompareFilesTool


def request(left: str = "a.txt", right: str = "b.txt") -> ToolUse:
    return ToolUse(
        "toolu_compare",
        COMPARE_FILES_TOOL_NAME,
        ToolArguments.from_mapping({"left": left, "right": right}),
    )


def test_compare_files_returns_unified_diff(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("one\nthree\n", encoding="utf-8")

    result = CompareFilesTool(tmp_path).execute(request())

    assert not result.is_error
    assert "--- a/a.txt" in result.content
    assert "+++ b/b.txt" in result.content
    assert "-two" in result.content
    assert "+three" in result.content


def test_compare_files_rejects_same_path_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to("a.txt")

    assert CompareFilesTool(tmp_path).execute(request("a.txt", "a.txt")).is_error
    result = CompareFilesTool(tmp_path).execute(request("a.txt", "link.txt"))
    assert result.is_error
    assert "symbolic links" in result.content
