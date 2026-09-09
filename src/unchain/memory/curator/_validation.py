from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlparse

from unchain.journal.models import ModelValidationError, _required_text


_HOST_ROOTS = frozenset(
    {
        "applications",
        "bin",
        "cores",
        "dev",
        "documents and settings",
        "etc",
        "home",
        "library",
        "mnt",
        "network",
        "opt",
        "private",
        "program files",
        "program files (x86)",
        "programdata",
        "proc",
        "root",
        "run",
        "sbin",
        "sys",
        "system",
        "tmp",
        "users",
        "usr",
        "var",
        "volumes",
        "windows",
    }
)
_WINDOWS_DRIVE_RE = re.compile(r"^/[A-Za-z]:(?:/|$)")
_URL_CREDENTIAL_SEGMENT_RE = re.compile(
    r"^(?:auth|authorization|bearer|code|cookie|credential|jwt|key|passwd|"
    r"password|sas|secret|sig|signature|token)s?[0-9]*$"
)
_URL_CREDENTIAL_COMPOUNDS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "apisecret",
        "authtoken",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "encryptionkey",
        "githubtoken",
        "idtoken",
        "oauthsecret",
        "oauthtoken",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "secretkey",
        "sessioncookie",
        "sessiontoken",
        "signingkey",
        "webhooksecret",
    }
)


def _fully_unquote_url_component(value: str) -> str:
    decoded = value
    for _ in range(16):
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    if unquote(decoded) != decoded:
        raise ModelValidationError(
            "link candidate URL contains credential-like nested encoding"
        )
    return decoded


def _url_key_is_sensitive(value: str) -> bool:
    compatible = unicodedata.normalize(
        "NFKC",
        _fully_unquote_url_component(value),
    )
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", compatible)
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.casefold()).strip("_")
    segments = tuple(segment for segment in normalized.split("_") if segment)
    collapsed = "".join(segments)
    if collapsed in _URL_CREDENTIAL_COMPOUNDS:
        return True
    if any(_URL_CREDENTIAL_SEGMENT_RE.fullmatch(segment) for segment in segments):
        return True
    return any(
        left in {"access", "api", "encryption", "private", "signing"}
        and re.fullmatch(r"keys?[0-9]*", right) is not None
        for left, right in zip(segments, segments[1:])
    )


def _url_component_has_sensitive_key(value: str) -> bool:
    decoded = _fully_unquote_url_component(value)
    for field in re.split(r"[&;?]", decoded):
        if _url_key_is_sensitive(field.split("=", 1)[0]):
            return True
    return False


def _url_path_has_embedded_credential(parsed: Any) -> bool:
    try:
        unicode_host = unicodedata.normalize("NFKC", parsed.hostname or "")
        host = unicode_host.encode("idna").decode("ascii").casefold().rstrip(".")
    except (UnicodeError, ValueError):
        return True
    path = unicodedata.normalize(
        "NFKC",
        _fully_unquote_url_component(parsed.path),
    ).casefold()
    if host == "hooks.slack.com" and path.startswith("/services/"):
        return True
    if host in {"discord.com", "discordapp.com"} and re.match(
        r"^/api(?:/v[0-9]+)?/webhooks/[^/]+/[^/]+",
        path,
    ):
        return True
    if host.endswith(".webhook.office.com") or (
        host == "outlook.office.com" and "/webhook/" in path
    ):
        return True
    if host == "maker.ifttt.com" and "/with/key/" in path:
        return True
    if host == "api.telegram.org" and re.match(r"^/bot[^/]+/", path):
        return True
    segments = tuple(segment for segment in path.split("/") if segment)
    if any(
        "=" in segment and _url_component_has_sensitive_key(segment)
        for segment in segments
    ):
        return True
    return any(
        candidate and _url_key_is_sensitive(marker)
        for marker, candidate in zip(segments, segments[1:])
    )


def canonical_candidate_path(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("target_path must be text")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise ModelValidationError("target_path contains control characters")
    normalized = unicodedata.normalize("NFKC", value)
    lower = normalized.casefold()
    if (
        normalized != normalized.strip()
        or unquote(normalized) != normalized
        or not normalized.startswith("/")
        or normalized.startswith("//")
        or "\\" in normalized
        or lower.startswith("file:")
        or _WINDOWS_DRIVE_RE.match(normalized)
        or len(normalized) > 2048
    ):
        raise ModelValidationError("target_path must be a canonical virtual path")
    segments = normalized.split("/")[1:]
    if normalized != "/" and any(segment in ("", ".", "..") for segment in segments):
        raise ModelValidationError("target_path contains an invalid segment")
    if segments and (
        segments[0].casefold() in _HOST_ROOTS
        or segments[0].casefold() == "file:"
        or segments[0].endswith(":")
    ):
        raise ModelValidationError("target_path looks like a host filesystem path")
    return normalized


def canonical_candidate_link_url(value: Any) -> str:
    link_url = _required_text(value, "link_url", maximum=8192)
    parsed = urlparse(link_url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ModelValidationError("link candidates require an HTTP(S) URL only")
    if parsed.username is not None or parsed.password is not None:
        raise ModelValidationError("link candidate URLs cannot contain credentials")
    if (
        _url_component_has_sensitive_key(parsed.query)
        or _url_component_has_sensitive_key(parsed.fragment)
        or _url_path_has_embedded_credential(parsed)
    ):
        raise ModelValidationError("link candidate URLs cannot contain credentials")
    return link_url


__all__ = ["canonical_candidate_link_url", "canonical_candidate_path"]
