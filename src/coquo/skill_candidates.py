"""Durable inactive Skill candidates from explicit proposals or public downloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Mapping
from urllib.parse import urlsplit
import zipfile

import yaml

from coquo.core.skill_authoring import (
    SkillAuthoringScope,
    SkillCreationProposal,
    canonical_skill_candidate_id,
)
from coquo.skills.authoring import SkillImportResult, import_skill
from coquo.skills.catalog import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_RESOURCE_BYTES,
    MAX_SKILL_RESOURCE_DIRECTORIES,
    MAX_SKILL_RESOURCE_PATH_CHARACTERS,
    MAX_SKILL_RESOURCE_TOTAL_BYTES,
    MAX_SKILL_RESOURCES,
    SkillCatalogError,
    SkillManifest,
    SkillResource,
    canonical_skill_name,
    load_skill_package,
)
from coquo.tools.download_file import (
    DOWNLOAD_FILE_TIMEOUT_SECONDS,
    MAX_DOWNLOAD_FILE_BYTES,
)
from coquo.tools.web_transport import (
    PinnedWebGetTransport,
    WebGetTransport,
    WebTransportError,
    canonical_public_web_url,
)


SKILL_CANDIDATE_VERSION = 1
MAX_SKILL_CANDIDATES = 128
MAX_SKILL_ARCHIVE_RATIO = 100
_EVENT_FILE_LIMIT = 128 * 1024
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_WORKFLOW_FINGERPRINT = re.compile(r"^workflow-v1-[0-9a-f]{64}$")


class SkillCandidateSource(StrEnum):
    GENERATED = "generated"
    EVOLUTION = "evolution"
    REMOTE_RAW = "remote-raw"
    REMOTE_ZIP = "remote-zip"


class SkillCandidateStatus(StrEnum):
    PENDING = "pending"
    INSTALLED = "installed"
    REVOKED = "revoked"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class SkillCandidateInfo:
    candidate_id: str
    source: SkillCandidateSource
    status: SkillCandidateStatus
    package_path: Path
    manifest: SkillManifest
    resources: tuple[SkillResource, ...]
    requested_scope: str | None
    owner_session_id: str | None
    proposal_turn_sequence: int | None
    source_sha256: str
    source_url: str | None
    installed_scope: str | None = None
    installed_lock_digest: str | None = None
    evolution_candidate_id: str | None = None
    source_trace_ids: tuple[str, ...] = ()
    pattern_fingerprint: str | None = None


class SkillCandidateStore:
    """Workspace-local candidate quarantine that never participates in Skill scanning."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        transport: WebGetTransport | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.environment = dict(os.environ if environment is None else environment)
        self.root = self.workspace / ".coquo" / "skill-candidates" / "v1"
        self._transport = transport or PinnedWebGetTransport()

    def create_generated(
        self,
        proposal: SkillCreationProposal,
        *,
        owner_session_id: str,
        turn_sequence: int,
    ) -> SkillCandidateInfo:
        if type(proposal) is not SkillCreationProposal:
            raise ValueError("generated Skill candidate proposal is invalid")
        if not isinstance(owner_session_id, str) or not owner_session_id:
            raise ValueError("generated Skill candidate owner is invalid")
        if type(turn_sequence) is not int or turn_sequence <= 0:
            raise ValueError("generated Skill candidate Turn sequence is invalid")
        return self._create(
            candidate_id=proposal.candidate_id,
            source=SkillCandidateSource.GENERATED,
            files={"SKILL.md": proposal.skill_file},
            requested_scope=proposal.scope.value,
            owner_session_id=owner_session_id,
            proposal_turn_sequence=turn_sequence,
            source_sha256=hashlib.sha256(proposal.skill_file).hexdigest(),
            source_url=None,
            proposal_context_id=proposal.context_id,
            proposal_tool_use_id=proposal.tool_use_id,
        )

    def create_evolution(
        self,
        *,
        evolution_candidate_id: str,
        name: str,
        description: str,
        instructions: str,
        allowed_tools: tuple[str, ...] | None,
        source_trace_ids: tuple[str, ...],
        pattern_fingerprint: str,
        scope: str = "project",
    ) -> SkillCandidateInfo:
        """Quarantine one Host-generated declarative Skill from an Evolution candidate."""
        if (
            not isinstance(evolution_candidate_id, str)
            or not evolution_candidate_id
            or len(evolution_candidate_id) > 64
            or not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(instructions, str)
            or not isinstance(pattern_fingerprint, str)
            or not source_trace_ids
            or scope not in {"workspace", "project"}
            or not _UUID4.fullmatch(evolution_candidate_id)
            or not _WORKFLOW_FINGERPRINT.fullmatch(pattern_fingerprint)
        ):
            raise SkillCatalogError("evolution-invalid", "Evolution Skill provenance is invalid")
        canonical_skill_name(name)
        if any(
            not isinstance(item, str) or _UUID4.fullmatch(item) is None for item in source_trace_ids
        ):
            raise SkillCatalogError(
                "evolution-invalid", "Evolution Skill trace provenance is invalid"
            )
        body = instructions if instructions.endswith("\n") else instructions + "\n"
        allowed = ""
        if allowed_tools is not None:
            allowed = "allowed-tools:\n" + "".join(f"  - {tool}\n" for tool in allowed_tools)
        skill_file = (
            "---\n"
            "manifest-version: 1\n"
            f"name: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"{allowed}"
            "---\n"
            f"{body}"
        ).encode("utf-8")
        candidate_id = _evolution_candidate_id(evolution_candidate_id, name, skill_file)
        return self._create(
            candidate_id=candidate_id,
            source=SkillCandidateSource.EVOLUTION,
            files={"SKILL.md": skill_file},
            requested_scope=scope,
            owner_session_id=None,
            proposal_turn_sequence=None,
            source_sha256=hashlib.sha256(skill_file).hexdigest(),
            source_url=None,
            proposal_context_id=None,
            proposal_tool_use_id=None,
            evolution_provenance={
                "version": 1,
                "evolution_candidate_id": evolution_candidate_id,
                "source_trace_ids": list(source_trace_ids),
                "pattern_fingerprint": pattern_fingerprint,
            },
        )

    def fetch(self, url: str) -> SkillCandidateInfo:
        """Fetch one public raw SKILL.md or ZIP into inactive quarantine."""
        try:
            canonical = canonical_public_web_url(url)
        except WebTransportError as error:
            raise SkillCatalogError(error.result_code, str(error)) from None
        parsed = urlsplit(canonical)
        if parsed.scheme != "https":
            raise SkillCatalogError("skill-fetch-scheme", "Skill fetch requires HTTPS")
        if parsed.query:
            raise SkillCatalogError(
                "skill-fetch-query",
                "Skill fetch URL must not contain a query string or credential-like token",
            )
        try:
            response = self._transport.fetch(
                canonical,
                timeout_seconds=DOWNLOAD_FILE_TIMEOUT_SECONDS,
                max_response_bytes=MAX_DOWNLOAD_FILE_BYTES,
            )
        except WebTransportError as error:
            raise SkillCatalogError(error.result_code, str(error)) from None
        if not 200 <= response.status_code < 300:
            raise SkillCatalogError(
                "skill-fetch-http", f"Skill fetch received HTTP {response.status_code}"
            )
        final = urlsplit(response.final_url)
        if final.scheme != "https" or final.query:
            raise SkillCatalogError(
                "skill-fetch-redirect",
                "Skill fetch final URL must remain HTTPS and contain no query string",
            )
        source_sha256 = hashlib.sha256(response.body).hexdigest()
        if response.body.startswith(b"PK\x03\x04"):
            source = SkillCandidateSource.REMOTE_ZIP
            files = _files_from_zip(response.body)
        else:
            source = SkillCandidateSource.REMOTE_RAW
            files = {"SKILL.md": _validate_raw_skill(response.body)}
        name = _declared_skill_name(files["SKILL.md"])
        candidate_id = _remote_candidate_id(source, response.final_url, source_sha256, name)
        return self._create(
            candidate_id=candidate_id,
            source=source,
            files=files,
            requested_scope=None,
            owner_session_id=None,
            proposal_turn_sequence=None,
            source_sha256=source_sha256,
            source_url=response.final_url,
            proposal_context_id=None,
            proposal_tool_use_id=None,
        )

    def list(self) -> tuple[SkillCandidateInfo, ...]:
        if not self.root.exists():
            return ()
        if self.root.is_symlink() or not self.root.is_dir():
            raise SkillCatalogError("candidate-root-invalid", "Skill candidate root is invalid")
        candidates: list[SkillCandidateInfo] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_dir():
                raise SkillCatalogError(
                    "candidate-entry-invalid", "Skill candidate root contains an invalid entry"
                )
            candidates.append(self.inspect(path.name))
            if len(candidates) > MAX_SKILL_CANDIDATES:
                raise SkillCatalogError(
                    "candidate-count-limit",
                    f"Skill candidate store exceeds {MAX_SKILL_CANDIDATES} candidates",
                )
        return tuple(candidates)

    def inspect(self, candidate_id: str) -> SkillCandidateInfo:
        canonical_skill_candidate_id(candidate_id)
        directory = self.root / candidate_id
        _validate_candidate_directory(self.root, directory)
        metadata = _read_json_file(directory / "candidate.json", 64 * 1024)
        if not isinstance(metadata, dict) or set(metadata) != {
            "candidate-id",
            "candidate-version",
            "manifest-fingerprint",
            "name",
            "owner-session-id",
            "proposal-turn-sequence",
            "proposal-context-id",
            "proposal-tool-use-id",
            "requested-scope",
            "source",
            "source-sha256",
            "source-url",
        }:
            raise SkillCatalogError("candidate-invalid", "Skill candidate metadata is invalid")
        if metadata["candidate-version"] != SKILL_CANDIDATE_VERSION:
            raise SkillCatalogError("candidate-invalid", "Skill candidate version is unsupported")
        if metadata["candidate-id"] != candidate_id:
            raise SkillCatalogError("candidate-invalid", "Skill candidate identity does not match")
        if (
            not isinstance(metadata["source-sha256"], str)
            or len(metadata["source-sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in metadata["source-sha256"])
            or metadata["requested-scope"] not in {None, "workspace", "project"}
            or (
                metadata["owner-session-id"] is not None
                and not isinstance(metadata["owner-session-id"], str)
            )
            or (
                metadata["proposal-turn-sequence"] is not None
                and (
                    type(metadata["proposal-turn-sequence"]) is not int
                    or metadata["proposal-turn-sequence"] <= 0
                )
            )
            or (metadata["source-url"] is not None and not isinstance(metadata["source-url"], str))
        ):
            raise SkillCatalogError("candidate-invalid", "Skill candidate metadata is invalid")
        try:
            source = SkillCandidateSource(metadata["source"])
        except (TypeError, ValueError):
            raise SkillCatalogError(
                "candidate-invalid", "Skill candidate source is invalid"
            ) from None
        name = canonical_skill_name(metadata["name"])
        package = directory / "package" / name
        manifest, resources = load_skill_package(package)
        if manifest.fingerprint != metadata["manifest-fingerprint"]:
            raise SkillCatalogError("candidate-drift", "Skill candidate package changed")
        package_skill_file = _read_regular(package / "SKILL.md", MAX_SKILL_FILE_BYTES)
        evolution_candidate_id = None
        source_trace_ids: tuple[str, ...] = ()
        pattern_fingerprint = None
        if source is SkillCandidateSource.GENERATED:
            try:
                reconstructed = SkillCreationProposal(
                    name=manifest.name,
                    description=manifest.description,
                    scope=SkillAuthoringScope(metadata["requested-scope"]),
                    allowed_tools=manifest.allowed_tools,
                    instructions=manifest.instructions,
                    context_id=metadata["proposal-context-id"],
                    tool_use_id=metadata["proposal-tool-use-id"],
                )
            except (TypeError, ValueError):
                raise SkillCatalogError(
                    "candidate-invalid", "Generated Skill candidate provenance is invalid"
                ) from None
            if (
                reconstructed.candidate_id != candidate_id
                or hashlib.sha256(package_skill_file).hexdigest() != metadata["source-sha256"]
            ):
                raise SkillCatalogError(
                    "candidate-drift", "Generated Skill candidate provenance changed"
                )
        elif source is SkillCandidateSource.EVOLUTION:
            provenance = _read_json_file(directory / "evolution-provenance.json", 16 * 1024)
            if (
                not isinstance(provenance, dict)
                or set(provenance)
                != {"version", "evolution_candidate_id", "source_trace_ids", "pattern_fingerprint"}
                or provenance.get("version") != 1
                or not isinstance(provenance.get("evolution_candidate_id"), str)
                or _UUID4.fullmatch(provenance.get("evolution_candidate_id", "")) is None
                or not isinstance(provenance.get("source_trace_ids"), list)
                or not provenance["source_trace_ids"]
                or not all(
                    isinstance(item, str) and _UUID4.fullmatch(item) is not None
                    for item in provenance["source_trace_ids"]
                )
                or not isinstance(provenance.get("pattern_fingerprint"), str)
                or _WORKFLOW_FINGERPRINT.fullmatch(provenance["pattern_fingerprint"]) is None
            ):
                raise SkillCatalogError(
                    "candidate-invalid", "Evolution Skill provenance is invalid"
                )
            evolution_candidate_id = provenance["evolution_candidate_id"]
            source_trace_ids = tuple(provenance["source_trace_ids"])
            pattern_fingerprint = provenance["pattern_fingerprint"]
            if (
                metadata["requested-scope"] not in {"workspace", "project"}
                or metadata["owner-session-id"] is not None
                or metadata["proposal-turn-sequence"] is not None
                or metadata["proposal-context-id"] is not None
                or metadata["proposal-tool-use-id"] is not None
                or not isinstance(metadata["source-url"], type(None))
            ):
                raise SkillCatalogError("candidate-invalid", "Evolution Skill metadata is invalid")
        elif (
            metadata["requested-scope"] is not None
            or metadata["owner-session-id"] is not None
            or metadata["proposal-turn-sequence"] is not None
            or metadata["proposal-context-id"] is not None
            or metadata["proposal-tool-use-id"] is not None
            or not isinstance(metadata["source-url"], str)
            or _remote_candidate_id(
                source,
                metadata["source-url"],
                metadata["source-sha256"],
                manifest.name,
            )
            != candidate_id
        ):
            raise SkillCatalogError(
                "candidate-invalid", "Remote Skill candidate provenance is invalid"
            )
        events = _read_events(directory / "events.jsonl", candidate_id)
        status = SkillCandidateStatus.PENDING
        installed_scope = None
        installed_lock_digest = None
        for event in events:
            kind = event["event"]
            if kind == "installed":
                status = SkillCandidateStatus.INSTALLED
                installed_scope = event["scope"]
                installed_lock_digest = event["lock-digest"]
            elif kind == "rejected":
                status = SkillCandidateStatus.REJECTED
            elif kind == "revoked":
                status = SkillCandidateStatus.REVOKED
            elif kind == "archived":
                status = SkillCandidateStatus.ARCHIVED
        return SkillCandidateInfo(
            candidate_id=candidate_id,
            source=source,
            status=status,
            package_path=package,
            manifest=manifest,
            resources=resources,
            requested_scope=metadata["requested-scope"],
            owner_session_id=metadata["owner-session-id"],
            proposal_turn_sequence=metadata["proposal-turn-sequence"],
            source_sha256=metadata["source-sha256"],
            source_url=metadata["source-url"],
            installed_scope=installed_scope,
            installed_lock_digest=installed_lock_digest,
            evolution_candidate_id=evolution_candidate_id,
            source_trace_ids=source_trace_ids,
            pattern_fingerprint=pattern_fingerprint,
        )

    def install(
        self,
        candidate_id: str,
        *,
        scope: str | None = None,
        expected_owner_session_id: str | None = None,
        evolution_approved: bool = False,
    ) -> SkillImportResult:
        candidate = self.inspect(candidate_id)
        if candidate.status is not SkillCandidateStatus.PENDING:
            raise SkillCatalogError("candidate-not-pending", "Skill candidate is not pending")
        if candidate.source is SkillCandidateSource.EVOLUTION and not evolution_approved:
            raise SkillCatalogError(
                "evolution-approval-required",
                "Evolution Skill candidates require Evolution evaluation and approval",
            )
        if expected_owner_session_id is not None and (
            candidate.source is not SkillCandidateSource.GENERATED
            or candidate.owner_session_id != expected_owner_session_id
        ):
            raise SkillCatalogError(
                "candidate-owner-mismatch", "Generated Skill candidate belongs to another Session"
            )
        selected_scope = scope or candidate.requested_scope
        if selected_scope not in {"workspace", "project", "user"}:
            raise SkillCatalogError("invalid-scope", "Skill install scope is invalid")
        if candidate.requested_scope is not None and selected_scope != candidate.requested_scope:
            raise SkillCatalogError(
                "candidate-scope-mismatch", "Skill candidate scope does not match its proposal"
            )
        result = import_skill(
            self.workspace,
            candidate.package_path,
            scope=selected_scope,
            environment=self.environment,
        )
        try:
            _append_event(
                self.root / candidate_id / "events.jsonl",
                {
                    "candidate-id": candidate_id,
                    "event": "installed",
                    "lock-digest": result.lock.digest,
                    "scope": selected_scope,
                    "version": 1,
                },
            )
        except BaseException as error:
            raise SkillCatalogError(
                "candidate-install-partial",
                "Skill package and lock were installed, but candidate status could not be committed",
            ) from error
        return result

    def reject(self, candidate_id: str) -> SkillCandidateInfo:
        candidate = self.inspect(candidate_id)
        if candidate.status is not SkillCandidateStatus.PENDING:
            raise SkillCatalogError("candidate-not-pending", "Skill candidate is not pending")
        _append_event(
            self.root / candidate_id / "events.jsonl",
            {
                "candidate-id": candidate_id,
                "event": "rejected",
                "version": 1,
            },
        )
        return self.inspect(candidate_id)

    def revoke(self, candidate_id: str) -> SkillCandidateInfo:
        """Remove one exact installed package from the active inventory for rollback."""
        candidate = self.inspect(candidate_id)
        if candidate.status is not SkillCandidateStatus.INSTALLED:
            raise SkillCatalogError("candidate-not-installed", "Skill candidate is not installed")
        from coquo.skills.authoring import skill_lock_root, skill_root

        _, root = skill_root(
            self.workspace, candidate.installed_scope or "project", self.environment
        )
        package = root / candidate.manifest.name
        lock = (
            skill_lock_root(
                self.workspace, candidate.installed_scope or "project", self.environment
            )
            / f"{candidate.manifest.name}.json"
        )
        current, resources = load_skill_package(package)
        if (
            current.fingerprint != candidate.manifest.fingerprint
            or resources != candidate.resources
        ):
            raise SkillCatalogError("candidate-drift", "Installed Skill changed before rollback")
        _remove_installed_package(package, lock)
        _append_event(
            self.root / candidate_id / "events.jsonl",
            {
                "candidate-id": candidate_id,
                "event": "revoked",
                "lock-digest": candidate.installed_lock_digest,
                "scope": candidate.installed_scope,
                "version": 1,
            },
        )
        return self.inspect(candidate_id)

    def archive(self, candidate_id: str) -> SkillCandidateInfo:
        """Archive a non-active quarantine record without deleting its audit evidence."""
        candidate = self.inspect(candidate_id)
        if candidate.status in {SkillCandidateStatus.INSTALLED, SkillCandidateStatus.ARCHIVED}:
            raise SkillCatalogError(
                "candidate-not-archivable", "Skill candidate cannot be archived"
            )
        _append_event(
            self.root / candidate_id / "events.jsonl",
            {"candidate-id": candidate_id, "event": "archived", "version": 1},
        )
        return self.inspect(candidate_id)

    def _create(
        self,
        *,
        candidate_id: str,
        source: SkillCandidateSource,
        files: Mapping[str, bytes],
        requested_scope: str | None,
        owner_session_id: str | None,
        proposal_turn_sequence: int | None,
        source_sha256: str,
        source_url: str | None,
        proposal_context_id: str | None,
        proposal_tool_use_id: str | None,
        evolution_provenance: Mapping[str, object] | None = None,
    ) -> SkillCandidateInfo:
        canonical_skill_candidate_id(candidate_id)
        if "SKILL.md" not in files:
            raise SkillCatalogError("candidate-invalid", "Skill candidate has no SKILL.md")
        name = _declared_skill_name(files["SKILL.md"])
        _ensure_private_directory(self.root)
        if len(tuple(self.root.iterdir())) >= MAX_SKILL_CANDIDATES:
            raise SkillCatalogError(
                "candidate-count-limit",
                f"Skill candidate store allows at most {MAX_SKILL_CANDIDATES} candidates",
            )
        directory = self.root / candidate_id
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            raise SkillCatalogError(
                "candidate-exists", f"Skill candidate already exists: {candidate_id}"
            ) from None
        created: list[Path] = []
        try:
            package = directory / "package" / name
            _ensure_private_directory(package)
            for relative, raw in sorted(files.items()):
                target = _safe_candidate_target(package, relative)
                _ensure_private_directory(target.parent)
                _write_exclusive(target, raw)
                created.append(target)
            manifest, resources = load_skill_package(package)
            metadata = {
                "candidate-id": candidate_id,
                "candidate-version": SKILL_CANDIDATE_VERSION,
                "manifest-fingerprint": manifest.fingerprint,
                "name": manifest.name,
                "owner-session-id": owner_session_id,
                "proposal-turn-sequence": proposal_turn_sequence,
                "proposal-context-id": proposal_context_id,
                "proposal-tool-use-id": proposal_tool_use_id,
                "requested-scope": requested_scope,
                "source": source.value,
                "source-sha256": source_sha256,
                "source-url": source_url,
            }
            _write_exclusive_json(directory / "candidate.json", metadata)
            if evolution_provenance is not None:
                _write_exclusive_json(directory / "evolution-provenance.json", evolution_provenance)
                created.append(directory / "evolution-provenance.json")
            _write_exclusive(
                directory / "events.jsonl",
                _json_line(
                    {
                        "candidate-id": candidate_id,
                        "event": "created",
                        "version": 1,
                    }
                ),
            )
            _fsync_tree(directory)
            return self.inspect(candidate_id)
        except BaseException:
            _remove_created_candidate(directory)
            raise


def _remote_candidate_id(
    source: SkillCandidateSource, url: str, source_sha256: str, name: str
) -> str:
    payload = json.dumps(
        {"name": name, "sha256": source_sha256, "source": source.value, "url": url},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "skc-v1-" + hashlib.sha256(b"coquo-remote-skill-v1\0" + payload).hexdigest()


def _evolution_candidate_id(evolution_candidate_id: str, name: str, skill_file: bytes) -> str:
    payload = json.dumps(
        {
            "evolution_candidate_id": evolution_candidate_id,
            "name": name,
            "sha256": hashlib.sha256(skill_file).hexdigest(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "skc-v1-" + hashlib.sha256(b"coquo-evolution-skill-v1\0" + payload).hexdigest()


def _validate_candidate_directory(root: Path, directory: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise SkillCatalogError("candidate-root-invalid", "Skill candidate root is invalid")
    if directory.is_symlink() or not directory.is_dir():
        raise SkillCatalogError("candidate-missing", "Skill candidate does not exist")


def _validate_raw_skill(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_SKILL_FILE_BYTES:
        raise SkillCatalogError("skill-fetch-size", "Remote SKILL.md exceeds its byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillCatalogError("invalid-utf8", "Remote SKILL.md must be strict UTF-8") from None
    if "\x00" in text or "\r" in text:
        raise SkillCatalogError("skill-fetch-text", "Remote SKILL.md contains invalid text")
    return raw


def _declared_skill_name(raw: bytes) -> str:
    raw = _validate_raw_skill(raw)
    text = raw.decode("utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SkillCatalogError("frontmatter-invalid", "Remote Skill frontmatter is invalid")
    metadata = text[4:].split("\n---\n", 1)[0]
    try:
        value = yaml.safe_load(metadata)
    except yaml.YAMLError:
        raise SkillCatalogError(
            "invalid-yaml", "Remote Skill frontmatter is invalid YAML"
        ) from None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise SkillCatalogError("invalid-name", "Remote Skill name is missing")
    return canonical_skill_name(value["name"])


def _files_from_zip(raw: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
    except (OSError, zipfile.BadZipFile):
        raise SkillCatalogError("skill-archive-invalid", "Remote Skill ZIP is invalid") from None
    with archive:
        entries = archive.infolist()
        files: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        seen: set[str] = set()
        folded: set[str] = set()
        directory_count = 0
        expanded_total = 0
        for entry in entries:
            name = entry.filename
            if not name or "\\" in name or "\x00" in name or name.startswith("/"):
                raise SkillCatalogError("skill-archive-path", "Skill ZIP contains an unsafe path")
            path = PurePosixPath(name.rstrip("/"))
            if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
                raise SkillCatalogError("skill-archive-path", "Skill ZIP contains an unsafe path")
            normalized = path.as_posix()
            if normalized in seen or normalized.casefold() in folded:
                raise SkillCatalogError(
                    "skill-archive-duplicate", "Skill ZIP contains duplicate paths"
                )
            seen.add(normalized)
            folded.add(normalized.casefold())
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise SkillCatalogError(
                    "skill-archive-entry", "Skill ZIP entries must be regular files or directories"
                )
            if entry.flag_bits & 0x1:
                raise SkillCatalogError(
                    "skill-archive-encrypted", "Encrypted Skill ZIP is unsupported"
                )
            if entry.is_dir():
                directory_count += 1
                continue
            if len(normalized) > MAX_SKILL_RESOURCE_PATH_CHARACTERS + 128:
                raise SkillCatalogError("skill-archive-path", "Skill ZIP path is too long")
            if entry.file_size > max(MAX_SKILL_FILE_BYTES, MAX_SKILL_RESOURCE_BYTES):
                raise SkillCatalogError("skill-archive-size", "Skill ZIP entry exceeds its limit")
            if entry.file_size > 1024 and (
                entry.compress_size == 0
                or entry.file_size / entry.compress_size > MAX_SKILL_ARCHIVE_RATIO
            ):
                raise SkillCatalogError(
                    "skill-archive-ratio", "Skill ZIP entry exceeds the compression ratio limit"
                )
            expanded_total += entry.file_size
            files.append((path, entry))
        if directory_count > MAX_SKILL_RESOURCE_DIRECTORIES or len(files) > MAX_SKILL_RESOURCES + 1:
            raise SkillCatalogError("skill-archive-count", "Skill ZIP contains too many entries")
        if expanded_total > MAX_SKILL_FILE_BYTES + MAX_SKILL_RESOURCE_TOTAL_BYTES:
            raise SkillCatalogError("skill-archive-size", "Skill ZIP expanded content is too large")
        skill_paths = [path for path, _ in files if path.name == "SKILL.md"]
        if len(skill_paths) != 1:
            raise SkillCatalogError(
                "skill-archive-root", "Skill ZIP must contain exactly one SKILL.md"
            )
        prefix = skill_paths[0].parent
        if len(prefix.parts) > 1:
            raise SkillCatalogError(
                "skill-archive-root", "Skill ZIP may use at most one wrapper directory"
            )
        selected: dict[str, bytes] = {}
        for path, entry in files:
            try:
                relative = path.relative_to(prefix) if prefix.parts else path
            except ValueError:
                raise SkillCatalogError(
                    "skill-archive-root", "Skill ZIP contains files outside its package root"
                ) from None
            if not relative.parts:
                continue
            relative_text = relative.as_posix()
            if (
                relative_text != "SKILL.md"
                and len(relative_text) > MAX_SKILL_RESOURCE_PATH_CHARACTERS
            ):
                raise SkillCatalogError("skill-archive-path", "Skill resource path is too long")
            try:
                selected[relative_text] = archive.read(entry)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                raise SkillCatalogError(
                    "skill-archive-invalid", "Skill ZIP could not be read"
                ) from None
        _validate_raw_skill(selected.get("SKILL.md", b""))
        return selected


def _safe_candidate_target(package: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillCatalogError("candidate-path-invalid", "Skill candidate path is invalid")
    return package.joinpath(*path.parts)


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise SkillCatalogError("candidate-root-invalid", "Skill candidate root is invalid")
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if directory.is_symlink() or not directory.is_dir():
                raise SkillCatalogError(
                    "candidate-root-invalid", "Skill candidate root changed during creation"
                ) from None


def _write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise SkillCatalogError("candidate-write-failed", "Skill candidate write failed") from error
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("candidate write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive_json(path: Path, value: Mapping[str, object]) -> None:
    _write_exclusive(path, _json_line(value))


def _json_line(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _append_event(path: Path, value: Mapping[str, object]) -> None:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SkillCatalogError(
            "candidate-event-failed", "Skill candidate event append failed"
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _EVENT_FILE_LIMIT:
            raise SkillCatalogError("candidate-invalid", "Skill candidate event log is invalid")
        raw = _json_line(value)
        if info.st_size + len(raw) > _EVENT_FILE_LIMIT:
            raise SkillCatalogError("candidate-invalid", "Skill candidate event log is too large")
        written = os.write(descriptor, raw)
        if written != len(raw):
            raise OSError("candidate event append was incomplete")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _read_json_file(path: Path, limit: int) -> object:
    raw = _read_regular(path, limit)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise SkillCatalogError("candidate-invalid", "Skill candidate JSON is invalid") from None


def _read_events(path: Path, candidate_id: str) -> tuple[dict[str, object], ...]:
    raw = _read_regular(path, _EVENT_FILE_LIMIT)
    events: list[dict[str, object]] = []
    terminal = False
    for line in raw.splitlines():
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise SkillCatalogError(
                "candidate-invalid", "Skill candidate event is invalid"
            ) from None
        if not isinstance(event, dict) or event.get("candidate-id") != candidate_id:
            raise SkillCatalogError(
                "candidate-invalid", "Skill candidate event identity is invalid"
            )
        kind = event.get("event")
        expected_fields = {
            "created": {"candidate-id", "event", "version"},
            "rejected": {"candidate-id", "event", "version"},
            "archived": {"candidate-id", "event", "version"},
            "installed": {
                "candidate-id",
                "event",
                "lock-digest",
                "scope",
                "version",
            },
            "revoked": {
                "candidate-id",
                "event",
                "lock-digest",
                "scope",
                "version",
            },
        }.get(kind)
        if expected_fields is None or set(event) != expected_fields or event.get("version") != 1:
            raise SkillCatalogError("candidate-invalid", "Skill candidate event schema is invalid")
        if kind == "installed" and (
            event.get("scope") not in {"workspace", "project", "user"}
            or not isinstance(event.get("lock-digest"), str)
            or len(event["lock-digest"]) != 64
        ):
            raise SkillCatalogError("candidate-invalid", "Skill install event is invalid")
        if not events and kind != "created":
            raise SkillCatalogError("candidate-invalid", "Skill candidate has no creation event")
        if events and kind not in {"installed", "revoked", "rejected", "archived"}:
            raise SkillCatalogError("candidate-invalid", "Skill candidate event kind is invalid")
        if terminal:
            raise SkillCatalogError(
                "candidate-invalid", "Skill candidate has events after terminal state"
            )
        terminal = kind in {"rejected", "archived"}
        events.append(event)
    if not events:
        raise SkillCatalogError("candidate-invalid", "Skill candidate event log is empty")
    return tuple(events)


def _read_regular(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SkillCatalogError("candidate-missing", "Skill candidate does not exist") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise SkillCatalogError("candidate-invalid", "Skill candidate file is invalid")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
        if (
            len(raw) != after.st_size
            or len(raw) > limit
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise SkillCatalogError("candidate-drift", "Skill candidate file changed while reading")
        return raw
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories = [Path(path) for path, _, _ in os.walk(root, followlinks=False)]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_installed_package(package: Path, lock: Path) -> None:
    """Remove only a package whose exact identity was checked by the caller."""
    if package.is_symlink() or not package.is_dir() or lock.is_symlink():
        raise SkillCatalogError("rollback-target-invalid", "Installed Skill target is invalid")
    if lock.exists() and not lock.is_file():
        raise SkillCatalogError("rollback-target-invalid", "Skill import lock is invalid")
    for root, directories, files in os.walk(package, topdown=False, followlinks=False):
        current = Path(root)
        for name in files:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise SkillCatalogError(
                    "rollback-target-invalid", "Skill package contains an invalid file"
                )
            path.unlink()
        for name in directories:
            path = current / name
            if path.is_symlink() or not path.is_dir():
                raise SkillCatalogError(
                    "rollback-target-invalid", "Skill package contains an invalid directory"
                )
            path.rmdir()
    package.rmdir()
    if lock.exists():
        lock.unlink()
    _fsync_directory(package.parent)
    if lock.parent.exists():
        _fsync_directory(lock.parent)


def _remove_created_candidate(directory: Path) -> None:
    if not directory.exists() or directory.is_symlink():
        return
    for root, directories, files in os.walk(directory, topdown=False, followlinks=False):
        current = Path(root)
        for name in files:
            path = current / name
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        for name in directories:
            path = current / name
            if not path.is_symlink():
                path.rmdir()
    directory.rmdir()
