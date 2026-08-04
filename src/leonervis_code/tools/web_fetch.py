"""Bounded public-page retrieval with deterministic text or Markdown extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
import hashlib
import json
import re

from leonervis_code.core.actions import ActionPrecondition
from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.core.permissions import PermissionAction
from leonervis_code.tools.web_transport import (
    PinnedWebGetTransport,
    WebGetTransport,
    WebTransportError,
    canonical_public_web_url,
)

WEB_FETCH_TOOL_NAME = "web_fetch"
WEB_FETCH_TIMEOUT_SECONDS = 20
MAX_WEB_FETCH_RESPONSE_BYTES = 512 * 1024
MAX_WEB_FETCH_OUTPUT_BYTES = 64 * 1024
WEB_FETCH_TRUNCATION_MARKER = "\n[truncated]\n"
_CHARSET = re.compile(r"(?:^|;)\s*charset=\s*[\"']?([^;\s\"']+)", re.IGNORECASE)


class WebFetchFormat(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"


class WebFetchPreparationError(ValueError):
    pass


class WebFetchOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class PreparedWebFetch:
    request: ToolUse
    url: str
    format: WebFetchFormat
    action: PermissionAction
    precondition: ActionPrecondition


@dataclass(frozen=True)
class WebFetchExecutionResult:
    tool_result: ToolResult
    outcome: WebFetchOutcome
    result_code: str
    audit_message: str


def web_fetch_model_definition() -> dict[str, object]:
    return {
        "name": WEB_FETCH_TOOL_NAME,
        "description": (
            "Fetch one public HTTP or HTTPS page with a bounded GET and return deterministic "
            "text or Markdown. The Host rejects credentials, non-public addresses, unsafe "
            "redirects, compressed bodies, unsupported MIME types, and oversized responses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Public HTTP or HTTPS URL.",
                },
                "format": {"type": "string", "enum": [item.value for item in WebFetchFormat]},
            },
            "required": ["url", "format"],
            "additionalProperties": False,
        },
    }


def web_fetch_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(web_fetch_model_definition())


class WebFetchTool:
    def __init__(self, transport: WebGetTransport | None = None) -> None:
        self._transport = transport or PinnedWebGetTransport()

    def prepare(self, request: ToolUse) -> PreparedWebFetch:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != WEB_FETCH_TOOL_NAME or set(arguments) != {"format", "url"}:
                raise ValueError
            url = arguments["url"]
            selected_format = arguments["format"]
            if not isinstance(url, str) or not isinstance(selected_format, str):
                raise ValueError
            format_value = WebFetchFormat(selected_format)
            canonical = canonical_public_web_url(url)
        except (AttributeError, ValueError):
            raise WebFetchPreparationError("web_fetch input is malformed") from None
        except WebTransportError as error:
            raise WebFetchPreparationError(str(error)) from None
        precondition = self._precondition(canonical, format_value)
        return PreparedWebFetch(
            request,
            canonical,
            format_value,
            PermissionAction.NETWORK_READ,
            precondition,
        )

    def revalidate(self, prepared: PreparedWebFetch) -> ActionPrecondition:
        if type(prepared) is not PreparedWebFetch:
            raise ValueError("prepared web_fetch is invalid")
        return self._precondition(prepared.url, prepared.format)

    def execute_detailed(self, prepared: PreparedWebFetch) -> WebFetchExecutionResult:
        if type(prepared) is not PreparedWebFetch:
            raise ValueError("prepared web_fetch is invalid")
        try:
            response = self._transport.fetch(
                prepared.url,
                timeout_seconds=WEB_FETCH_TIMEOUT_SECONDS,
                max_response_bytes=MAX_WEB_FETCH_RESPONSE_BYTES,
            )
            if not 200 <= response.status_code < 300:
                return self._failed(
                    prepared, "web_http_error", f"web_fetch received HTTP {response.status_code}"
                )
            mime = _mime_type(response.content_type)
            if not _supported_mime(mime):
                return self._failed(
                    prepared, "web_mime_unsupported", "web_fetch response MIME type is unsupported"
                )
            text = _decode_response(response.body, response.content_type)
            if mime in {"text/html", "application/xhtml+xml"}:
                extracted = _extract_html(text, prepared.format)
            else:
                extracted = text
            header = (
                json.dumps(
                    {
                        "bytes": len(response.body),
                        "content_type": mime,
                        "format": prepared.format.value,
                        "redirects": response.redirects,
                        "url": response.final_url,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
            content, truncated = _bound_output(header, extracted)
        except WebTransportError as error:
            outcome = WebFetchOutcome.PARTIAL if error.delivery_unknown else WebFetchOutcome.FAILED
            return WebFetchExecutionResult(
                ToolResult(prepared.request.tool_use_id, str(error), is_error=True),
                outcome,
                error.result_code,
                str(error),
            )
        except WebFetchPreparationError as error:
            return self._failed(prepared, "web_content_invalid", str(error))
        return WebFetchExecutionResult(
            ToolResult(prepared.request.tool_use_id, content, truncated=truncated),
            WebFetchOutcome.SUCCEEDED,
            "web_fetched",
            f"web_fetch fetched {len(response.body)} bytes after {response.redirects} redirects",
        )

    @staticmethod
    def _precondition(url: str, format_value: WebFetchFormat) -> ActionPrecondition:
        payload = json.dumps(
            {
                "format": format_value.value,
                "max_response_bytes": MAX_WEB_FETCH_RESPONSE_BYTES,
                "timeout_seconds": WEB_FETCH_TIMEOUT_SECONDS,
                "url": url,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return ActionPrecondition.expected_configuration(hashlib.sha256(payload).hexdigest())

    @staticmethod
    def _failed(prepared: PreparedWebFetch, code: str, message: str) -> WebFetchExecutionResult:
        return WebFetchExecutionResult(
            ToolResult(prepared.request.tool_use_id, message, is_error=True),
            WebFetchOutcome.FAILED,
            code,
            message,
        )


def _mime_type(value: str) -> str:
    return value.partition(";")[0].strip().lower()


def _supported_mime(value: str) -> bool:
    return value in {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
    } or value.endswith(("+json", "+xml"))


def _decode_response(body: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type)
    charset = "utf-8" if match is None else match.group(1).lower()
    if charset not in {"utf-8", "utf8", "us-ascii", "ascii"}:
        raise WebFetchPreparationError("web_fetch response charset is unsupported")
    try:
        text = body.decode("utf-8-sig" if charset in {"utf-8", "utf8"} else "ascii")
    except UnicodeDecodeError:
        raise WebFetchPreparationError("web_fetch response is not valid declared text") from None
    if "\x00" in text:
        raise WebFetchPreparationError("web_fetch response contains NUL")
    return text


class _ReadableHtmlParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template"}
    _BLOCK = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "header",
        "hr",
        "main",
        "nav",
        "p",
        "section",
        "table",
        "tr",
    }

    def __init__(self, output_format: WebFetchFormat) -> None:
        super().__init__(convert_charrefs=True)
        self.format = output_format
        self.parts: list[str] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "pre":
            self.pre_depth += 1
            if self.format is WebFetchFormat.MARKDOWN:
                self.parts.append("\n```\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")
            if self.format is WebFetchFormat.MARKDOWN:
                self.parts.append(f"{'#' * int(tag[1])} ")
        elif tag == "li":
            self.parts.append("\n- " if self.format is WebFetchFormat.MARKDOWN else "\n")
        elif tag == "code" and not self.pre_depth and self.format is WebFetchFormat.MARKDOWN:
            self.parts.append("`")
        elif tag == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "pre" and self.pre_depth:
            if self.format is WebFetchFormat.MARKDOWN:
                self.parts.append("\n```\n")
            self.pre_depth -= 1
        elif tag == "code" and not self.pre_depth and self.format is WebFetchFormat.MARKDOWN:
            self.parts.append("`")
        elif tag == "a" and self.links:
            href = self.links.pop()
            if (
                self.format is WebFetchFormat.MARKDOWN
                and href
                and not href.lower().startswith(("javascript:", "data:"))
            ):
                self.parts.append(f" ({href})")
        if tag in self._BLOCK or tag in {"li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data if self.pre_depth else re.sub(r"\s+", " ", data))


def _extract_html(value: str, output_format: WebFetchFormat) -> str:
    parser = _ReadableHtmlParser(output_format)
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        raise WebFetchPreparationError("web_fetch HTML could not be parsed safely") from None
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    output: list[str] = []
    blank = True
    for line in lines:
        if line:
            output.append(line)
            blank = False
        elif not blank:
            output.append("")
            blank = True
    return "\n".join(output).strip()


def _bound_output(header: str, content: str) -> tuple[str, bool]:
    prefix = f"{header}{content}"
    encoded = prefix.encode("utf-8")
    if len(encoded) <= MAX_WEB_FETCH_OUTPUT_BYTES:
        return prefix, False
    marker = WEB_FETCH_TRUNCATION_MARKER.encode("ascii")
    selected = encoded[: MAX_WEB_FETCH_OUTPUT_BYTES - len(marker)]
    return selected.decode("utf-8", errors="ignore") + WEB_FETCH_TRUNCATION_MARKER, True
