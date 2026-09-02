from __future__ import annotations


import pytest

from coquo.skills.process_execution import (
    SkillProcessPolicy,
    SkillProcessRunner,
)


def test_skill_process_runner_uses_direct_argv_and_bounds_output(tmp_path):
    launcher = tmp_path / "launcher"
    launcher.write_text('#!/bin/sh\nexec "$@"\n')
    launcher.chmod(0o700)
    result = SkillProcessRunner(SkillProcessPolicy(launcher, max_output_bytes=4)).run(
        ("/bin/printf", "abcdef"), cwd=tmp_path
    )
    assert result.outcome == "completed"
    assert result.stdout == "abcd"
    assert result.truncated is True


def test_skill_process_runner_fails_closed_without_launcher(tmp_path):
    with pytest.raises(ValueError, match="launcher"):
        SkillProcessPolicy(tmp_path / "missing")
