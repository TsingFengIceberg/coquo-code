"""Local OAuth 2.1 PKCE lifecycle for remote MCP credentials."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode, urlsplit

from coquo.mcp.config import (
    McpConfigurationError,
    McpServerEntry,
    McpTransport,
    _atomic_write,
    _read_json,
)
from coquo.mcp.http_client import McpHttpTransport
from coquo.mcp.client import (
    _closed_object,
    _reject_json_constant,
    _validate_json_bounds,
)
from coquo.tools.web_transport import (
    PinnedWebGetTransport,
    WebTransportError,
    canonical_public_web_url,
)


MCP_OAUTH_SCHEMA_VERSION = 1
MAX_MCP_OAUTH_FILE_BYTES = 1024 * 1024
MAX_MCP_OAUTH_METADATA_BYTES = 256 * 1024
MCP_OAUTH_TIMEOUT_SECONDS = 15
MCP_OAUTH_PENDING_TTL_SECONDS = 10 * 60
MCP_OAUTH_EXPIRY_SKEW_SECONDS = 60


class McpOAuthError(RuntimeError):
    """Sanitized OAuth discovery, state, exchange, refresh, or storage failure."""


@dataclass(frozen=True)
class McpOAuthPending:
    server_key: str
    configuration_revision: int
    state: str
    verifier: str
    redirect_uri: str
    authorization_endpoint: str
    token_endpoint: str
    created_at: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "authorization_endpoint": self.authorization_endpoint,
            "configuration_revision": self.configuration_revision,
            "created_at": self.created_at,
            "redirect_uri": self.redirect_uri,
            "server_key": self.server_key,
            "state": self.state,
            "token_endpoint": self.token_endpoint,
            "verifier": self.verifier,
        }

    @classmethod
    def from_mapping(cls, value: object) -> McpOAuthPending:
        if not isinstance(value, dict) or set(value) != {
            "authorization_endpoint",
            "configuration_revision",
            "created_at",
            "redirect_uri",
            "server_key",
            "state",
            "token_endpoint",
            "verifier",
        }:
            raise McpOAuthError("MCP OAuth pending record is invalid")
        try:
            pending = cls(**value)
        except TypeError:
            raise McpOAuthError("MCP OAuth pending record is invalid") from None
        if (
            not isinstance(pending.server_key, str)
            or not pending.server_key
            or type(pending.configuration_revision) is not int
            or pending.configuration_revision < 1
            or not isinstance(pending.state, str)
            or not 32 <= len(pending.state) <= 512
            or _contains_control(pending.state)
            or not isinstance(pending.verifier, str)
            or not 43 <= len(pending.verifier) <= 512
            or _contains_control(pending.verifier)
            or type(pending.created_at) is not int
        ):
            raise McpOAuthError("MCP OAuth pending record is invalid")
        _validate_redirect_uri(pending.redirect_uri)
        _canonical_https(pending.authorization_endpoint)
        _canonical_https(pending.token_endpoint)
        return pending


@dataclass(frozen=True)
class McpOAuthToken:
    server_key: str
    configuration_revision: int
    access_token: str
    refresh_token: str | None
    expires_at: int | None
    scopes: tuple[str, ...]
    revision: int

    @property
    def expired(self) -> bool:
        return (
            self.expires_at is not None
            and self.expires_at <= int(time.time()) + MCP_OAUTH_EXPIRY_SKEW_SECONDS
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "access_token": self.access_token,
            "configuration_revision": self.configuration_revision,
            "expires_at": self.expires_at,
            "refresh_token": self.refresh_token,
            "revision": self.revision,
            "scopes": list(self.scopes),
            "server_key": self.server_key,
        }

    @classmethod
    def from_mapping(cls, value: object) -> McpOAuthToken:
        if not isinstance(value, dict) or set(value) != {
            "access_token",
            "configuration_revision",
            "expires_at",
            "refresh_token",
            "revision",
            "scopes",
            "server_key",
        }:
            raise McpOAuthError("MCP OAuth token record is invalid")
        scopes = value["scopes"]
        if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise McpOAuthError("MCP OAuth token scopes are invalid")
        try:
            token = cls(
                server_key=value["server_key"],
                configuration_revision=value["configuration_revision"],
                access_token=value["access_token"],
                refresh_token=value["refresh_token"],
                expires_at=value["expires_at"],
                scopes=tuple(scopes),
                revision=value["revision"],
            )
        except TypeError:
            raise McpOAuthError("MCP OAuth token record is invalid") from None
        _validate_token(token)
        return token


@dataclass(frozen=True)
class McpOAuthStatus:
    configured: bool
    pending: bool
    authorized: bool
    expired: bool
    token_revision: int | None


def default_mcp_oauth_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    configured = env.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / "coquo" / "mcp-oauth.json"


class McpOAuthManager:
    """Own bounded PKCE authorization state and refreshable user credentials."""

    def __init__(
        self,
        path: Path,
        environment: Mapping[str, str] | None = None,
        *,
        transport: McpHttpTransport | None = None,
        now: callable = time.time,
    ) -> None:
        self.path = Path(path)
        self._environment = dict(os.environ if environment is None else environment)
        self._transport = transport or PinnedWebGetTransport()
        self._now = now

    @classmethod
    def default(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        transport: McpHttpTransport | None = None,
    ) -> McpOAuthManager:
        return cls(default_mcp_oauth_path(environment), environment, transport=transport)

    def begin(self, entry: McpServerEntry, redirect_uri: str) -> str:
        configuration = _oauth_configuration(entry)
        redirect = _validate_redirect_uri(redirect_uri)
        authorization_endpoint, token_endpoint = self._discover(configuration.endpoint or "")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        pending = McpOAuthPending(
            server_key=_server_key(entry),
            configuration_revision=configuration.revision,
            state=state,
            verifier=verifier,
            redirect_uri=redirect,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            created_at=int(self._now()),
        )
        data = self._load()
        data["pending"][_server_key(entry)] = pending.as_mapping()
        self._write(data)
        query = {
            "client_id": configuration.oauth_client_id or "",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect,
            "response_type": "code",
            "state": state,
        }
        if configuration.oauth_scopes:
            query["scope"] = " ".join(configuration.oauth_scopes)
        return (
            authorization_endpoint
            + ("&" if "?" in authorization_endpoint else "?")
            + urlencode(query)
        )

    def complete(self, entry: McpServerEntry, *, code: str, state: str) -> McpOAuthToken:
        configuration = _oauth_configuration(entry)
        data = self._load()
        raw_pending = data["pending"].get(_server_key(entry))
        if raw_pending is None:
            raise McpOAuthError("MCP OAuth authorization is not pending")
        pending = McpOAuthPending.from_mapping(raw_pending)
        if (
            pending.configuration_revision != configuration.revision
            or pending.state != state
            or int(self._now()) - pending.created_at > MCP_OAUTH_PENDING_TTL_SECONDS
        ):
            raise McpOAuthError("MCP OAuth authorization state is stale or invalid")
        if not isinstance(code, str) or not code or len(code) > 8192 or _contains_control(code):
            raise McpOAuthError("MCP OAuth authorization code is invalid")
        payload = {
            "client_id": configuration.oauth_client_id or "",
            "code": code,
            "code_verifier": pending.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": pending.redirect_uri,
        }
        token_data = self._token_request(entry, pending.token_endpoint, payload)
        token = self._token_from_response(entry, token_data, prior=None)
        data["tokens"][_server_key(entry)] = token.as_mapping()
        data["pending"].pop(_server_key(entry), None)
        self._write(data)
        return token

    def access_token(self, entry: McpServerEntry) -> str | None:
        configuration = entry.configuration
        if configuration.oauth_client_id is None:
            return None
        data = self._load()
        raw = data["tokens"].get(_server_key(entry))
        if raw is None:
            return None
        token = McpOAuthToken.from_mapping(raw)
        if token.configuration_revision != configuration.revision:
            return None
        if token.expired:
            if token.refresh_token is None:
                return None
            _, token_endpoint = self._discover(configuration.endpoint or "")
            response = self._token_request(
                entry,
                token_endpoint,
                {
                    "client_id": configuration.oauth_client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                },
            )
            token = self._token_from_response(entry, response, prior=token)
            data["tokens"][_server_key(entry)] = token.as_mapping()
            self._write(data)
        return token.access_token

    def status(self, entry: McpServerEntry) -> McpOAuthStatus:
        configured = entry.configuration.oauth_client_id is not None
        if not configured:
            return McpOAuthStatus(False, False, False, False, None)
        data = self._load()
        pending = _server_key(entry) in data["pending"]
        raw = data["tokens"].get(_server_key(entry))
        if raw is None:
            return McpOAuthStatus(True, pending, False, False, None)
        token = McpOAuthToken.from_mapping(raw)
        current = token.configuration_revision == entry.configuration.revision
        return McpOAuthStatus(True, pending, current, current and token.expired, token.revision)

    def logout(self, entry: McpServerEntry) -> bool:
        data = self._load()
        key = _server_key(entry)
        changed = key in data["tokens"] or key in data["pending"]
        data["tokens"].pop(key, None)
        data["pending"].pop(key, None)
        if changed:
            self._write(data)
        return changed

    def _discover(self, resource: str) -> tuple[str, str]:
        parsed = urlsplit(resource)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        resource_metadata_url = (
            origin + "/.well-known/oauth-protected-resource" + parsed.path.rstrip("/")
        )
        resource_metadata = self._get_json(resource_metadata_url)
        declared_resource = resource_metadata.get("resource")
        if declared_resource is not None and declared_resource != resource:
            raise McpOAuthError("MCP OAuth protected-resource identity does not match")
        authorization_servers = resource_metadata.get("authorization_servers")
        if (
            not isinstance(authorization_servers, list)
            or len(authorization_servers) != 1
            or not isinstance(authorization_servers[0], str)
        ):
            raise McpOAuthError("MCP OAuth protected-resource metadata is unsupported")
        issuer = _canonical_https(authorization_servers[0])
        issuer_parts = urlsplit(issuer)
        metadata_url = (
            f"{issuer_parts.scheme}://{issuer_parts.netloc}/.well-known/oauth-authorization-server"
            + issuer_parts.path.rstrip("/")
        )
        metadata = self._get_json(metadata_url)
        if _canonical_https_field(metadata, "issuer") != issuer:
            raise McpOAuthError("MCP OAuth authorization-server issuer does not match")
        authorization_endpoint = _canonical_https_field(metadata, "authorization_endpoint")
        token_endpoint = _canonical_https_field(metadata, "token_endpoint")
        methods = metadata.get("code_challenge_methods_supported", [])
        if not isinstance(methods, list) or "S256" not in methods:
            raise McpOAuthError("MCP OAuth server does not advertise PKCE S256")
        return authorization_endpoint, token_endpoint

    def _get_json(self, url: str) -> dict[str, object]:
        try:
            response = self._transport.request(
                "GET",
                _canonical_https(url),
                headers={"Accept": "application/json"},
                body=None,
                timeout_seconds=MCP_OAUTH_TIMEOUT_SECONDS,
                max_response_bytes=MAX_MCP_OAUTH_METADATA_BYTES,
            )
        except WebTransportError:
            raise McpOAuthError("MCP OAuth metadata request failed") from None
        if response.status_code != 200:
            raise McpOAuthError("MCP OAuth metadata endpoint returned a non-success status")
        return _decode_json_object(response.body, "metadata")

    def _token_request(
        self,
        entry: McpServerEntry,
        endpoint: str,
        values: dict[str, str],
    ) -> dict[str, object]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        secret_env = entry.configuration.oauth_client_secret_env
        if secret_env is not None:
            secret = self._environment.get(secret_env, "")
            if not secret or _contains_control(secret):
                raise McpOAuthError("MCP OAuth client secret environment is unavailable")
            raw = f"{entry.configuration.oauth_client_id}:{secret}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        try:
            response = self._transport.request(
                "POST",
                _canonical_https(endpoint),
                headers=headers,
                body=urlencode(values).encode("ascii"),
                timeout_seconds=MCP_OAUTH_TIMEOUT_SECONDS,
                max_response_bytes=MAX_MCP_OAUTH_METADATA_BYTES,
            )
        except WebTransportError:
            raise McpOAuthError("MCP OAuth token request failed with unknown delivery") from None
        if response.status_code != 200:
            raise McpOAuthError("MCP OAuth token endpoint rejected the request")
        return _decode_json_object(response.body, "token response")

    def _token_from_response(
        self,
        entry: McpServerEntry,
        value: dict[str, object],
        *,
        prior: McpOAuthToken | None,
    ) -> McpOAuthToken:
        access_token = value.get("access_token")
        token_type = value.get("token_type")
        refresh_token = value.get("refresh_token", prior.refresh_token if prior else None)
        expires_in = value.get("expires_in")
        scope_value = value.get("scope")
        if token_type != "Bearer" and token_type != "bearer":
            raise McpOAuthError("MCP OAuth token type is unsupported")
        if type(expires_in) is not int or not 1 <= expires_in <= 365 * 24 * 60 * 60:
            raise McpOAuthError("MCP OAuth token expiry is missing or invalid")
        scopes = (
            tuple(sorted(scope_value.split()))
            if isinstance(scope_value, str)
            else entry.configuration.oauth_scopes
        )
        token = McpOAuthToken(
            server_key=_server_key(entry),
            configuration_revision=entry.configuration.revision,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(self._now()) + expires_in,
            scopes=scopes,
            revision=1 if prior is None else prior.revision + 1,
        )
        _validate_token(token)
        return token

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            data = _read_json(self.path)
        except McpConfigurationError as error:
            raise McpOAuthError("MCP OAuth credential store is unavailable") from error
        if data is None:
            return {"pending": {}, "tokens": {}}
        if (
            set(data) != {"pending", "schema_version", "tokens"}
            or data["schema_version"] != MCP_OAUTH_SCHEMA_VERSION
        ):
            raise McpOAuthError("MCP OAuth credential store schema is unsupported")
        if not isinstance(data["pending"], dict) or not isinstance(data["tokens"], dict):
            raise McpOAuthError("MCP OAuth credential store is invalid")
        if len(data["pending"]) > 64 or len(data["tokens"]) > 64:
            raise McpOAuthError("MCP OAuth credential store exceeds the entry limit")
        return {"pending": dict(data["pending"]), "tokens": dict(data["tokens"])}

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        try:
            _atomic_write(
                self.path,
                {
                    "pending": data["pending"],
                    "schema_version": MCP_OAUTH_SCHEMA_VERSION,
                    "tokens": data["tokens"],
                },
            )
        except McpConfigurationError as error:
            raise McpOAuthError("MCP OAuth credential store could not be written") from error


def _oauth_configuration(entry: McpServerEntry):  # noqa: ANN202
    configuration = entry.configuration
    if (
        configuration.transport is not McpTransport.STREAMABLE_HTTP
        or configuration.oauth_client_id is None
    ):
        raise McpOAuthError("MCP server is not configured for remote OAuth")
    if configuration.bearer_token_env is not None:
        raise McpOAuthError("MCP server uses static bearer authentication")
    return configuration


def _server_key(entry: McpServerEntry) -> str:
    return f"{entry.scope}:{entry.configuration.name}"


def _validate_token(token: McpOAuthToken) -> None:
    if (
        not isinstance(token.server_key, str)
        or not token.server_key
        or type(token.configuration_revision) is not int
        or token.configuration_revision < 1
        or not isinstance(token.access_token, str)
        or not token.access_token
        or len(token.access_token) > 64 * 1024
        or _contains_control(token.access_token)
        or (
            token.refresh_token is not None
            and (
                not isinstance(token.refresh_token, str)
                or not token.refresh_token
                or len(token.refresh_token) > 64 * 1024
                or _contains_control(token.refresh_token)
            )
        )
        or (token.expires_at is not None and type(token.expires_at) is not int)
        or type(token.revision) is not int
        or token.revision < 1
        or not isinstance(token.scopes, tuple)
        or len(token.scopes) > 32
        or tuple(sorted(set(token.scopes))) != token.scopes
        or any(
            not isinstance(scope, str)
            or not scope
            or len(scope) > 256
            or any(character.isspace() or ord(character) < 0x21 for character in scope)
            for scope in token.scopes
        )
    ):
        raise McpOAuthError("MCP OAuth token record is invalid")


def _validate_redirect_uri(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _contains_control(value)
    ):
        raise McpOAuthError("MCP OAuth redirect URI is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.fragment
    ):
        raise McpOAuthError("MCP OAuth redirect URI must be an HTTP loopback URI")
    return value


def _canonical_https(value: str) -> str:
    try:
        canonical = canonical_public_web_url(value)
    except WebTransportError:
        raise McpOAuthError("MCP OAuth endpoint is invalid") from None
    if urlsplit(canonical).scheme != "https":
        raise McpOAuthError("MCP OAuth endpoint must use HTTPS")
    return canonical


def _canonical_https_field(value: dict[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise McpOAuthError(f"MCP OAuth metadata is missing {field}")
    return _canonical_https(candidate)


def _decode_json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_json_constant,
        )
        _validate_json_bounds(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RuntimeError):
        raise McpOAuthError(f"MCP OAuth {label} is not valid JSON") from None
    if not isinstance(value, dict):
        raise McpOAuthError(f"MCP OAuth {label} must be an object")
    return value


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
