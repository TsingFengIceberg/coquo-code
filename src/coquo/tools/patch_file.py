"""Prepared structured exact multi-edit patches over one bounded workspace file."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path

from coquo.core.actions import ActionPrecondition, ActionPreconditionKind
from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction
from coquo.tools._workspace_paths import WorkspacePathFailure, validate_workspace_path
from coquo.tools.write_file import (
    MAX_OVERWRITE_SOURCE_BYTES,
    WriteFilePartialEffectError,
    WriteFilePreparationError,
    WriteFileTool,
)

PATCH_FILE_TOOL_NAME = "patch_file"
MAX_PATCH_FILE_EDITS = 16
MAX_PATCH_FILE_TEXT_CHARACTERS = 4096
MAX_PATCH_FILE_TEXT_BYTES = 4096
MAX_PATCH_FILE_SOURCE_BYTES = MAX_OVERWRITE_SOURCE_BYTES
MAX_PATCH_FILE_RESULT_BYTES = MAX_OVERWRITE_SOURCE_BYTES


@dataclass(frozen=True)
class PreparedPatchFile:
    """One side-effect-free multi-edit candidate bound to its original content digest."""

    request: ToolUse
    relative_path: str
    original_content: bytes
    content: bytes
    replacements: int
    action: PermissionAction
    precondition: ActionPrecondition


class PatchFilePreparationError(ValueError):
    """A safe hard-bound rejection before a patch is permission-eligible."""


class PatchFileOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class PatchFileExecutionResult:
    tool_result: ToolResult
    outcome: PatchFileOutcome
    result_code: str
    audit_message: str


@dataclass(frozen=True)
class _ExactEdit:
    old_text: str
    new_text: str
    start: int
    end: int


class PatchFileTool:
    """Apply several unique non-overlapping exact replacements in one atomic overwrite."""

    def __init__(self, workspace: Path) -> None:
        self._write_boundary = WriteFileTool(workspace)

    def prepare(self, request: ToolUse) -> PreparedPatchFile:
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"edits", "path"}:
                raise ValueError
            raw_path = arguments["path"]
            raw_edits = arguments["edits"]
            if not isinstance(raw_path, str) or not isinstance(raw_edits, list):
                raise ValueError
        except (AttributeError, ValueError):
            raise PatchFilePreparationError("patch_file input is malformed") from None

        if not 1 <= len(raw_edits) <= MAX_PATCH_FILE_EDITS:
            raise PatchFilePreparationError(
                f"patch_file edits must contain 1 to {MAX_PATCH_FILE_EDITS} exact edits"
            )
        try:
            parts = validate_workspace_path(
                raw_path,
                tool_name=PATCH_FILE_TOOL_NAME,
                allow_root=False,
            )
        except WorkspacePathFailure as error:
            raise PatchFilePreparationError(str(error)) from None
        relative_path = "/".join(parts)

        parsed: list[tuple[str, str]] = []
        for index, raw_edit in enumerate(raw_edits):
            if not isinstance(raw_edit, dict) or set(raw_edit) != {"new_text", "old_text"}:
                raise PatchFilePreparationError(f"patch_file edits[{index}] is malformed")
            old_text = raw_edit["old_text"]
            new_text = raw_edit["new_text"]
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise PatchFilePreparationError(f"patch_file edits[{index}] is malformed")
            if not old_text:
                raise PatchFilePreparationError(
                    f"patch_file edits[{index}].old_text must not be empty"
                )
            if old_text == new_text:
                raise PatchFilePreparationError(
                    f"patch_file edits[{index}] replacement must change the file"
                )
            self._validate_text(index, "old_text", old_text)
            self._validate_text(index, "new_text", new_text)
            parsed.append((old_text, new_text))

        try:
            _, target = self._write_boundary._target(relative_path)
            observed = self._write_boundary._observe(target)
        except WriteFilePreparationError as error:
            raise PatchFilePreparationError(_patch_message(error)) from None
        if observed is None:
            raise PatchFilePreparationError("patch_file target must already exist")

        source = observed.content.decode("utf-8")
        edits: list[_ExactEdit] = []
        for index, (old_text, new_text) in enumerate(parsed):
            start = source.find(old_text)
            if start < 0:
                raise PatchFilePreparationError(f"patch_file edits[{index}].old_text was not found")
            if source.find(old_text, start + 1) >= 0:
                raise PatchFilePreparationError(
                    f"patch_file edits[{index}].old_text matches more than once"
                )
            edits.append(_ExactEdit(old_text, new_text, start, start + len(old_text)))

        ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start < previous.end:
                raise PatchFilePreparationError("patch_file exact edits overlap")

        chunks: list[str] = []
        cursor = 0
        for edit in ordered:
            chunks.append(source[cursor : edit.start])
            chunks.append(edit.new_text)
            cursor = edit.end
        chunks.append(source[cursor:])
        candidate = "".join(chunks)
        if candidate == source:
            raise PatchFilePreparationError("patch_file replacements must change the file")
        try:
            encoded = candidate.encode("utf-8")
        except UnicodeEncodeError:
            raise PatchFilePreparationError("patch_file result must be valid UTF-8") from None
        if len(encoded) > MAX_PATCH_FILE_RESULT_BYTES:
            raise PatchFilePreparationError(
                f"patch_file result exceeds {MAX_PATCH_FILE_RESULT_BYTES} bytes"
            )

        return PreparedPatchFile(
            request=request,
            relative_path=relative_path,
            original_content=observed.content,
            content=encoded,
            replacements=len(edits),
            action=PermissionAction.WORKSPACE_OVERWRITE,
            precondition=ActionPrecondition.expected_state(observed.digest),
        )

    @staticmethod
    def _validate_text(index: int, field: str, value: str) -> None:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PatchFilePreparationError(
                f"patch_file edits[{index}].{field} must be valid UTF-8"
            ) from None
        if len(value) > MAX_PATCH_FILE_TEXT_CHARACTERS or len(encoded) > MAX_PATCH_FILE_TEXT_BYTES:
            raise PatchFilePreparationError(
                f"patch_file edits[{index}].{field} exceeds {MAX_PATCH_FILE_TEXT_BYTES} bytes"
            )

    def refresh_precondition(self, prepared: PreparedPatchFile) -> ActionPrecondition:
        try:
            _, target = self._write_boundary._target(prepared.relative_path)
            observed = self._write_boundary._observe(target)
        except WriteFilePreparationError as error:
            raise PatchFilePreparationError(_patch_message(error)) from None
        if observed is None:
            return ActionPrecondition.path_absent()
        return ActionPrecondition.expected_state(observed.digest)

    def execute(self, prepared: PreparedPatchFile) -> ToolResult:
        return self.execute_detailed(prepared).tool_result

    def execute_detailed(self, prepared: PreparedPatchFile) -> PatchFileExecutionResult:
        request = prepared.request
        try:
            if prepared.precondition.kind != ActionPreconditionKind.EXPECTED_STATE_SHA256:
                raise PatchFilePreparationError("patch_file precondition is invalid")
            assert prepared.precondition.fingerprint is not None
            _, target = self._write_boundary._target(prepared.relative_path)
            self._write_boundary._overwrite(
                target,
                prepared.content,
                prepared.precondition.fingerprint,
            )
        except WriteFilePartialEffectError as error:
            message = _patch_message(error)
            result_code = (
                "patched_durability_unknown"
                if error.result_code == "overwritten_durability_unknown"
                else error.result_code
            )
            return PatchFileExecutionResult(
                ToolResult(request.tool_use_id, message, is_error=True),
                PatchFileOutcome.PARTIAL,
                result_code,
                message,
            )
        except (WriteFilePreparationError, PatchFilePreparationError) as error:
            message = _patch_message(error)
            return PatchFileExecutionResult(
                ToolResult(request.tool_use_id, message, is_error=True),
                PatchFileOutcome.FAILED,
                "patch_not_applied",
                message,
            )

        payload = json.dumps(
            {
                "bytes_written": len(prepared.content),
                "operation": "patched",
                "path": prepared.relative_path,
                "replacements": prepared.replacements,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return PatchFileExecutionResult(
            ToolResult(request.tool_use_id, f"{payload}\n"),
            PatchFileOutcome.SUCCEEDED,
            "patched",
            f"patch_file patched {prepared.relative_path} with {prepared.replacements} replacements",
        )


def patch_file_model_definition() -> dict[str, object]:
    return {
        "name": PATCH_FILE_TOOL_NAME,
        "description": (
            "Atomically apply 1 to 16 unique, non-overlapping exact text replacements to one "
            "existing bounded UTF-8 workspace file. Every old_text is matched against the same "
            "original snapshot. The Host applies overwrite permission, approval, no-symlink, "
            "exact-state, size, and atomic-install checks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of an existing text file.",
                },
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_PATCH_FILE_EDITS,
                    "description": (
                        "Exact replacements, all resolved against one original file snapshot."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_PATCH_FILE_TEXT_CHARACTERS,
                                "description": (
                                    "Exact text that must occur exactly once in the original file."
                                ),
                            },
                            "new_text": {
                                "type": "string",
                                "maxLength": MAX_PATCH_FILE_TEXT_CHARACTERS,
                                "description": "Exact replacement text, which may be empty.",
                            },
                        },
                        "required": ["old_text", "new_text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["path", "edits"],
            "additionalProperties": False,
        },
    }


def patch_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(patch_file_model_definition())


def _patch_message(error: BaseException) -> str:
    return str(error).replace("write_file", "patch_file", 1)
