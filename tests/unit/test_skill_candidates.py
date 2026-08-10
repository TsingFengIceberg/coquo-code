from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import stat
import warnings
import zipfile

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.skill_authoring import (
    SKILL_PROPOSE_CREATE_TOOL_NAME,
    SkillCreationProposal,
)
from coquo.skill_candidates import (
    SkillCandidateSource,
    SkillCandidateStatus,
    SkillCandidateStore,
)
from coquo.skills import SkillCatalogError
from coquo.tools.web_transport import WebHttpResponse


SKILL = (
    b"---\nmanifest-version: 1\nname: remote-demo\n"
    b"description: Remote demo\n---\nFollow the reviewed workflow.\n"
)


@dataclass
class FakeTransport:
    body: bytes
    final_url: str = "https://example.com/SKILL.md"

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> WebHttpResponse:
        assert url == "https://example.com/SKILL.md"
        assert timeout_seconds == 30
        assert max_response_bytes == 16 * 1024 * 1024
        return WebHttpResponse(200, "text/plain", "", self.body, self.final_url, 0)


def proposal() -> SkillCreationProposal:
    return SkillCreationProposal.from_request(
        ToolUse(
            "skill-create-1",
            SKILL_PROPOSE_CREATE_TOOL_NAME,
            ToolArguments.from_mapping(
                {
                    "allowed_tools": ["read_file", "run_command"],
                    "description": "Validate one Python release",
                    "instructions": "Inspect metadata, run tests, and report evidence.",
                    "name": "python-release",
                    "scope": "project",
                }
            ),
        ),
        "ctx-v15-" + "a" * 64,
    )


def zip_bytes(*, unsafe: bool = False) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("package/SKILL.md", SKILL)
        archive.writestr("../escape.txt" if unsafe else "package/guide.md", "Guide.\n")
    return buffer.getvalue()


def special_zip(kind: str) -> bytes:
    buffer = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("package/SKILL.md", SKILL)
            if kind == "duplicate":
                archive.writestr("package/guide.md", "one")
                archive.writestr("package/guide.md", "two")
            elif kind == "multiple-root":
                archive.writestr("other/SKILL.md", SKILL)
            elif kind == "symlink":
                entry = zipfile.ZipInfo("package/link")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "target")
            elif kind == "ratio":
                archive.writestr("package/repetitive.txt", b"0" * (48 * 1024))
            else:
                raise AssertionError("unknown special ZIP kind")
    return buffer.getvalue()


def test_generated_candidate_is_inactive_and_installs_through_exact_import_lock(
    tmp_path: Path,
) -> None:
    store = SkillCandidateStore(tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "config")})
    created = store.create_generated(proposal(), owner_session_id="session-one", turn_sequence=1)

    assert created.status is SkillCandidateStatus.PENDING
    assert created.source is SkillCandidateSource.GENERATED
    assert created.manifest.name == "python-release"
    assert not (tmp_path / ".agents" / "skills" / "python-release").exists()

    result = store.install(created.candidate_id, expected_owner_session_id="session-one")
    installed = store.inspect(created.candidate_id)
    assert installed.status is SkillCandidateStatus.INSTALLED
    assert installed.installed_scope == "project"
    assert installed.installed_lock_digest == result.lock.digest
    assert (tmp_path / ".agents" / "skills" / "python-release" / "SKILL.md").is_file()
    assert store.list() == (installed,)


def test_remote_raw_and_zip_stay_quarantined_until_explicit_install(tmp_path: Path) -> None:
    raw_store = SkillCandidateStore(tmp_path, {}, transport=FakeTransport(SKILL))
    raw = raw_store.fetch("https://example.com/SKILL.md")
    assert raw.source is SkillCandidateSource.REMOTE_RAW
    assert raw.status is SkillCandidateStatus.PENDING
    assert not (tmp_path / ".agents" / "skills" / "remote-demo").exists()

    other = tmp_path / "zip"
    other.mkdir()
    zip_store = SkillCandidateStore(other, {}, transport=FakeTransport(zip_bytes()))
    zipped = zip_store.fetch("https://example.com/SKILL.md")
    assert zipped.source is SkillCandidateSource.REMOTE_ZIP
    assert [resource.path for resource in zipped.resources] == ["guide.md"]


def test_remote_fetch_rejects_unsafe_urls_archives_and_redirect_queries(tmp_path: Path) -> None:
    store = SkillCandidateStore(tmp_path, {}, transport=FakeTransport(SKILL))
    with pytest.raises(SkillCatalogError, match="HTTPS"):
        store.fetch("http://example.com/SKILL.md")
    with pytest.raises(SkillCatalogError, match="query string"):
        store.fetch("https://example.com/SKILL.md?token=secret")

    unsafe = SkillCandidateStore(tmp_path, {}, transport=FakeTransport(zip_bytes(unsafe=True)))
    with pytest.raises(SkillCatalogError, match="unsafe path"):
        unsafe.fetch("https://example.com/SKILL.md")

    redirected = SkillCandidateStore(
        tmp_path,
        {},
        transport=FakeTransport(SKILL, "https://example.com/SKILL.md?token=secret"),
    )
    with pytest.raises(SkillCatalogError, match="final URL"):
        redirected.fetch("https://example.com/SKILL.md")


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("duplicate", "duplicate paths"),
        ("multiple-root", "exactly one SKILL.md"),
        ("symlink", "regular files or directories"),
        ("ratio", "compression ratio"),
    ],
)
def test_remote_zip_rejects_ambiguous_or_special_entries(
    tmp_path: Path, kind: str, message: str
) -> None:
    store = SkillCandidateStore(tmp_path, {}, transport=FakeTransport(special_zip(kind)))
    with pytest.raises(SkillCatalogError, match=message):
        store.fetch("https://example.com/SKILL.md")


def test_candidate_reject_and_package_drift_are_terminal(tmp_path: Path) -> None:
    store = SkillCandidateStore(tmp_path, {})
    created = store.create_generated(proposal(), owner_session_id="session-one", turn_sequence=1)
    rejected = store.reject(created.candidate_id)
    assert rejected.status is SkillCandidateStatus.REJECTED
    with pytest.raises(SkillCatalogError, match="not pending"):
        store.install(created.candidate_id)

    other = tmp_path / "other"
    other.mkdir()
    other_store = SkillCandidateStore(other, {})
    candidate = other_store.create_generated(
        proposal(), owner_session_id="session-one", turn_sequence=1
    )
    (candidate.package_path / "SKILL.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SkillCatalogError):
        other_store.inspect(candidate.candidate_id)


def test_direct_candidate_inspection_rejects_symlinked_candidate_directory(tmp_path: Path) -> None:
    store = SkillCandidateStore(tmp_path, {})
    candidate = store.create_generated(proposal(), owner_session_id="session-one", turn_sequence=1)
    directory = store.root / candidate.candidate_id
    moved = store.root / "moved"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)

    with pytest.raises(SkillCatalogError, match="does not exist"):
        store.inspect(candidate.candidate_id)
