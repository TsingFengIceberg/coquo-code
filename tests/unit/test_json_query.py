from pathlib import Path

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.json_query import JSON_QUERY_TOOL_NAME, JsonQueryTool


def request(pointer: str, path: str = "data.json") -> ToolUse:
    return ToolUse(
        "toolu_json",
        JSON_QUERY_TOOL_NAME,
        ToolArguments.from_mapping({"path": path, "pointer": pointer}),
    )


def test_json_query_resolves_escaped_pointer_and_root(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text('{"a/b":{"~key":[10,20]}}', encoding="utf-8")
    tool = JsonQueryTool(tmp_path)

    selected = tool.execute(request("/a~1b/~0key/1"))
    root = tool.execute(request(""))

    assert selected.content == '{"pointer":"/a~1b/~0key/1","value":20}\n'
    assert '"a/b"' in root.content


def test_json_query_rejects_duplicate_keys_and_invalid_pointer(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text('{"a":1,"a":2}', encoding="utf-8")
    duplicate = JsonQueryTool(tmp_path).execute(request("/a"))
    assert duplicate.is_error
    assert "duplicate" in duplicate.content

    (tmp_path / "data.json").write_text('{"items":[1]}', encoding="utf-8")
    invalid = JsonQueryTool(tmp_path).execute(request("/items/01"))
    assert invalid.is_error
    assert "array index" in invalid.content
