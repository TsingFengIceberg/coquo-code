from __future__ import annotations

import pytest

from coquo.core.approval_preview import (
    APPROVAL_PREVIEW_VERSION,
    MAX_APPROVAL_DIFF_BYTES,
    MAX_APPROVAL_DIFF_LINES,
    ApprovalPreview,
    ApprovalPreviewKind,
    build_file_change_preview,
    build_metadata_preview,
)

ACTION_DIGEST = f"act-v1-{'1' * 64}"


def test_create_preview_is_a_bounded_unified_diff() -> None:
    preview = build_file_change_preview(
        action_digest=ACTION_DIGEST,
        path="src/app.py",
        before=None,
        after=b'print("hello")\n',
    )

    assert preview == ApprovalPreview(
        action_digest=ACTION_DIGEST,
        kind=ApprovalPreviewKind.FILE_CHANGE,
        byte_count=15,
        body=('--- /dev/null\n+++ b/src/app.py\n@@ -0,0 +1 @@\n+print("hello")\n'),
    )


def test_overwrite_preview_marks_missing_final_newline() -> None:
    preview = build_file_change_preview(
        action_digest=ACTION_DIGEST,
        path="note.txt",
        before=b"before\n",
        after=b"after",
    )

    assert "-before\n" in preview.body
    assert "+after\n\\ No newline at end of file\n" in preview.body


def test_empty_create_and_unchanged_overwrite_remain_informed_actions() -> None:
    created = build_file_change_preview(
        action_digest=ACTION_DIGEST,
        path="empty.txt",
        before=None,
        after=b"",
    )
    overwritten = build_file_change_preview(
        action_digest=ACTION_DIGEST,
        path="same.txt",
        before=b"same\n",
        after=b"same\n",
    )

    assert "Empty file will be created" in created.body
    assert "Content is unchanged" in overwritten.body


def test_large_diff_is_explicitly_truncated_within_both_bounds() -> None:
    before = "".join(f"old-{index}-{'x' * 5000}\n" for index in range(200)).encode()
    after = "".join(f"new-{index}-{'y' * 5000}\n" for index in range(200)).encode()

    preview = build_file_change_preview(
        action_digest=ACTION_DIGEST,
        path="large.txt",
        before=before,
        after=after,
    )

    assert preview.truncated
    assert preview.body is not None
    assert len(preview.body.encode("utf-8")) <= MAX_APPROVAL_DIFF_BYTES
    assert preview.body.count("\n") <= MAX_APPROVAL_DIFF_LINES
    assert "[line truncated]" in preview.body


def test_metadata_preview_rejects_file_change_without_a_diff() -> None:
    with pytest.raises(ValueError, match="require a diff"):
        build_metadata_preview(
            action_digest=ACTION_DIGEST,
            kind=ApprovalPreviewKind.FILE_CHANGE,
        )


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalPreviewKind.WEB_FETCH,
        ApprovalPreviewKind.DIRECTORY_MOVE,
        ApprovalPreviewKind.FILE_DOWNLOAD,
    ),
)
def test_additional_action_previews_are_content_free(kind: ApprovalPreviewKind) -> None:
    preview = build_metadata_preview(action_digest=ACTION_DIGEST, kind=kind)

    assert preview.kind is kind
    assert preview.body is None
    assert preview.byte_count is None
    assert preview.version == APPROVAL_PREVIEW_VERSION == 6


@pytest.mark.parametrize("transport", ("stdio", "streamable-http"))
def test_mcp_preview_requires_one_known_transport(transport: str) -> None:
    preview = build_metadata_preview(
        action_digest=ACTION_DIGEST,
        kind=ApprovalPreviewKind.MCP_TOOL,
        transport=transport,
    )

    assert preview.transport == transport
    assert preview.version == APPROVAL_PREVIEW_VERSION == 6

    for invalid in (None, "sse"):
        with pytest.raises(ValueError, match="transport"):
            build_metadata_preview(
                action_digest=ACTION_DIGEST,
                kind=ApprovalPreviewKind.MCP_TOOL,
                transport=invalid,
            )
    with pytest.raises(ValueError, match="does not match"):
        build_metadata_preview(
            action_digest=ACTION_DIGEST,
            kind=ApprovalPreviewKind.COMMAND,
            transport="stdio",
        )


def test_web_search_preview_requires_a_supported_backend() -> None:
    tavily = build_metadata_preview(
        action_digest=ACTION_DIGEST,
        kind=ApprovalPreviewKind.WEB_SEARCH,
        backend="tavily",
    )
    assert tavily.backend == "tavily"
    assert tavily.version == APPROVAL_PREVIEW_VERSION == 6
    assert (
        build_metadata_preview(
            action_digest=ACTION_DIGEST,
            kind=ApprovalPreviewKind.WEB_SEARCH,
            backend="brave",
        ).backend
        == "brave"
    )

    for backend in (None, "other"):
        with pytest.raises(ValueError, match="backend"):
            build_metadata_preview(
                action_digest=ACTION_DIGEST,
                kind=ApprovalPreviewKind.WEB_SEARCH,
                backend=backend,
            )
    with pytest.raises(ValueError, match="does not match"):
        build_metadata_preview(
            action_digest=ACTION_DIGEST,
            kind=ApprovalPreviewKind.COMMAND,
            backend="tavily",
        )
    with pytest.raises(ValueError, match="version"):
        ApprovalPreview(
            action_digest=ACTION_DIGEST,
            kind=ApprovalPreviewKind.WEB_SEARCH,
            backend="tavily",
            version=1,
        )


def test_preview_rejects_unversioned_action_digest() -> None:
    with pytest.raises(ValueError, match="action digest"):
        ApprovalPreview(
            action_digest="1" * 64,
            kind=ApprovalPreviewKind.COMMAND,
        )
