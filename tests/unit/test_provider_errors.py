from __future__ import annotations

from types import SimpleNamespace

from coquo.providers.errors import (
    extract_upstream_error_metadata,
    safe_http_status,
    safe_upstream_text,
)


def test_upstream_metadata_keeps_standard_fields_without_raw_body() -> None:
    error = SimpleNamespace(
        status_code=503,
        request_id=None,
        response=SimpleNamespace(
            status_code=503,
            headers={"x-request-id": "req_503", "retry-after": "11"},
        ),
        body={
            "type": "error",
            "error": {
                "type": "overloaded_error",
                "code": "overloaded",
                "message": "service is busy",
                "secret": "must not be copied",
            },
        },
    )

    metadata = extract_upstream_error_metadata(error)

    assert metadata.http_status_code == 503
    assert metadata.upstream_error_type == "overloaded_error"
    assert metadata.upstream_error_code == "overloaded"
    assert metadata.upstream_message == "service is busy"
    assert metadata.request_id == "req_503"
    assert metadata.retry_after_seconds == 11
    assert "secret" not in repr(metadata)


def test_unknown_or_non_json_upstream_body_is_not_retained() -> None:
    error = SimpleNamespace(
        status_code=302,
        request_id="req_redirect",
        response=SimpleNamespace(status_code=302, headers={}),
        body="<html>redirect with credential=secret</html>",
    )

    metadata = extract_upstream_error_metadata(error)

    assert metadata.http_status_code == 302
    assert metadata.request_id == "req_redirect"
    assert metadata.upstream_error_code is None
    assert metadata.upstream_error_type is None
    assert metadata.upstream_message is None
    assert "secret" not in repr(metadata)


def test_upstream_text_rejects_terminal_controls_and_bounds_values() -> None:
    assert safe_http_status(302) == 302
    assert safe_http_status(99) is None
    assert safe_http_status(600) is None
    assert safe_http_status(True) is None
    assert safe_upstream_text("ok", maximum=2) == "ok"
    assert safe_upstream_text("abcdef", maximum=3) == "abc"
    assert safe_upstream_text("bad\x1b[2J", maximum=100) is None
