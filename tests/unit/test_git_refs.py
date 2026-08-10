from pathlib import Path
import subprocess

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.git_refs import GIT_REFS_TOOL_NAME, GitRefsTool


def git(workspace: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=workspace, check=True, capture_output=True)


def test_git_refs_lists_head_branch_and_tag(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-qm", "initial")
    git(tmp_path, "tag", "v1")
    branch = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    request = ToolUse("toolu_refs", GIT_REFS_TOOL_NAME, ToolArguments.from_mapping({}))

    result = GitRefsTool(tmp_path).execute(request)

    assert not result.is_error
    assert f'"branch":"{branch}","kind":"head"' in result.content
    assert f'"current":true,"kind":"branch","name":"{branch}"' in result.content
    assert '"kind":"tag","name":"v1"' in result.content
