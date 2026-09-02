"""Bounded HTTPS client for untrusted external job-source content."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from typing import Callable, Iterable
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = "AI-Driven-Job-Search/0.1 (+local career-source monitor)"


@dataclass(frozen=True)
class SafeHttpResponse:
    status: int
    url: str
    content_type: str
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class SafeHttpClient:
    """Fetch only allowlisted public HTTPS resources within strict bounds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 3,
        resolver: Callable[..., Iterable[tuple]] = socket.getaddrinfo,
        opener=None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self._resolver = resolver
        self._opener = opener or build_opener(_NoRedirect())

    def _validate_target(self, url: str, allowed_hosts: frozenset[str]) -> str:
        parsed = urlsplit(url)
        if parsed.scheme.casefold() != "https":
            raise ValueError("Source request must use HTTPS")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Source request has an invalid authority")
        host = parsed.hostname.casefold().rstrip(".")
        if host not in allowed_hosts:
            raise ValueError(f"Source request host is not allowlisted: {host}")
        if parsed.port not in {None, 443}:
            raise ValueError("Source request cannot use a custom port")
        addresses = {
            item[4][0]
            for item in self._resolver(host, 443, type=socket.SOCK_STREAM)
        }
        if not addresses:
            raise ValueError(f"Source request host did not resolve: {host}")
        if any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise ValueError("Source request resolved to a non-public IP address")
        return host

    def fetch(
        self,
        url: str,
        *,
        allowed_hosts: Iterable[str],
        accepted_content_types: Iterable[str],
    ) -> SafeHttpResponse:
        normalized_hosts = frozenset(host.casefold().rstrip(".") for host in allowed_hosts)
        accepted = tuple(value.casefold() for value in accepted_content_types)
        current_url = url

        for redirect_count in range(self.max_redirects + 1):
            self._validate_target(current_url, normalized_hosts)
            request = Request(
                current_url,
                headers={
                    "Accept": ", ".join(accepted),
                    "User-Agent": USER_AGENT,
                },
                method="GET",
            )
            try:
                response = self._opener.open(request, timeout=self.timeout_seconds)
            except HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise ValueError("Source redirect omitted Location") from exc
                if redirect_count >= self.max_redirects:
                    raise ValueError("Source exceeded the redirect limit") from exc
                current_url = urljoin(current_url, location)
                continue

            with response:
                status = int(response.status)
                final_url = response.geturl()
                self._validate_target(final_url, normalized_hosts)
                content_type = response.headers.get_content_type().casefold()
                if not any(
                    content_type == item or content_type.startswith(f"{item}+")
                    for item in accepted
                ):
                    raise ValueError(f"Unexpected source content type: {content_type}")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.max_response_bytes:
                    raise ValueError("Source response exceeds the size limit")
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise ValueError("Source response exceeds the size limit")
                return SafeHttpResponse(status, final_url, content_type, body)

        raise ValueError("Source exceeded the redirect limit")
